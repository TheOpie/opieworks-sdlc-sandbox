"""Eval harness and eval-gate (FR-52, FR-53, SCN-6). Owner: the testing stage.

A human runs run-evals on a machine with an agent harness. It runs each task
in sdlc/evals/tasks/ in a temporary git worktree and writes
sdlc/evals/results/latest.json with the raw logs and the watched-path hash.

The sdlc-checks CI job runs the eval-gate with no model. It reads the PR
head only as data: git trees and blobs, never a checkout and never a run.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path, PurePosixPath

from . import common, overlay, role
from .common import SdlcError, safe_join, safe_write_path, write_if_changed

TASKS_REL = "sdlc/evals/tasks"
RESULT_REL = "sdlc/evals/results/latest.json"
RESULT_FORMAT = 1
# The eval-gate watches AGENTS.md files and the sdlc/ folder. Work item
# artifacts and the eval results are not agent instructions, so they are
# not watched. A change to them alone does not need new evals, and the
# result file cannot change its own hash.
WATCHED_NAME = "AGENTS.md"
WATCHED_PREFIX = "sdlc/"
WATCH_EXCLUDE = ("sdlc/work/", "sdlc/evals/results/")
TASK_KEYS = {"role", "check", "prompt", "inputs", "setup"}
OUTPUT_ENV = "EVAL_OUTPUT"
ROLE_EXIT_ENV = "EVAL_ROLE_EXIT"


# Watched paths and the hash ------------------------------------------------------


def is_watched(path: str) -> bool:
    """Return True for a repo-relative POSIX path that the eval-gate watches."""
    if PurePosixPath(path).name == WATCHED_NAME:
        return True
    return path.startswith(WATCHED_PREFIX) and not path.startswith(WATCH_EXCLUDE)


def watch_hash(entries) -> str:
    """Return the SHA-256 of the watched blob entries (mode, path, blob id).

    The hash uses git blob IDs, so a local tree and the same tree read
    through the GitHub API give the same hash.
    """
    lines = sorted(f"{mode} {sha}\t{path}\n" for mode, path, sha in entries if is_watched(path))
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _watched_map(entries) -> dict[str, tuple[str, str]]:
    return {path: (mode, sha) for mode, path, sha in entries if is_watched(path)}


# Data sources: the PR head is data only ----------------------------------------


class GitSource:
    """Read trees and blobs from a local git repo. It never checks out a commit."""

    def __init__(self, repo: Path):
        self.repo = Path(repo)

    def _git(self, *args: str) -> bytes:
        result = subprocess.run(["git", *args], cwd=self.repo, capture_output=True)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise SdlcError(f"git {args[0]} failed: {detail[-1] if detail else 'no output'}")
        return result.stdout

    def blobs(self, rev: str) -> list[tuple[str, str, str]]:
        out = self._git("ls-tree", "-r", "-z", "--full-tree", rev)
        entries = []
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, _, path = record.partition(b"\t")
            mode, kind, sha = meta.decode("ascii").split()
            if kind == "blob":
                entries.append((mode, path.decode("utf-8", errors="surrogateescape"), sha))
        return entries

    def read(self, rev: str, path: str) -> bytes | None:
        for _mode, entry_path, sha in self.blobs(rev):
            if entry_path == path:
                return self._git("cat-file", "blob", sha)
        return None

    def merge_base(self, base: str, head: str) -> str:
        return self._git("merge-base", base, head).decode("ascii").strip()


class ApiSource:
    """Read trees and blobs through the GitHub API. CI uses it for the PR head."""

    def __init__(self, slug: str, env: dict[str, str] | None = None):
        self.slug = slug
        self.env = env
        self._trees: dict[str, list[tuple[str, str, str]]] = {}

    def _api(self, path: str):
        from . import github

        return github.api("GET", f"repos/{self.slug}/{path}", env=self.env)

    def blobs(self, rev: str) -> list[tuple[str, str, str]]:
        if rev not in self._trees:
            data = self._api(f"git/trees/{rev}?recursive=1")
            if data.get("truncated"):
                raise SdlcError(f"The GitHub tree of {rev} is too large to read in one call. The eval-gate cannot hash it.")
            self._trees[rev] = [
                (item["mode"], item["path"], item["sha"]) for item in data.get("tree", []) if item.get("type") == "blob"
            ]
        return self._trees[rev]

    def read(self, rev: str, path: str) -> bytes | None:
        for _mode, entry_path, sha in self.blobs(rev):
            if entry_path == path:
                data = self._api(f"git/blobs/{sha}")
                return base64.b64decode(data.get("content", ""))
        return None

    def merge_base(self, base: str, head: str) -> str:
        return self._api(f"compare/{base}...{head}")["merge_base_commit"]["sha"]


# Results ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    tasks_total: int
    tasks_passed: int
    watch_hash: str

    @property
    def rate(self) -> Fraction:
        return Fraction(self.tasks_passed, self.tasks_total)


def parse_result(data: bytes | str | None, where: str) -> EvalResult | None:
    """Parse latest.json. Return None when data is None. Raise SdlcError when it is not valid."""
    if data is None:
        return None
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise SdlcError(f"The eval result on {where} is not valid JSON.") from None
    total = obj.get("tasks_total") if isinstance(obj, dict) else None
    passed = obj.get("tasks_passed") if isinstance(obj, dict) else None
    digest = obj.get("watch_hash") if isinstance(obj, dict) else None
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (total, passed))
    if not ints or total < 1 or not 0 <= passed <= total or not isinstance(digest, str):
        raise SdlcError(f"The eval result on {where} needs tasks_total, tasks_passed, and watch_hash.")
    return EvalResult(total, passed, digest)


def _rate_text(result: EvalResult) -> str:
    return f"{result.tasks_passed} of {result.tasks_total} ({float(result.rate):.0%})"


@dataclass
class GateResult:
    ok: bool
    lines: list[str] = field(default_factory=list)


def eval_gate(source, base: str, head: str) -> GateResult:
    """Run the eval-gate for a PR from base to head (FR-52, FR-53).

    The gate fails when the PR changes a watched path and the head has no
    fresh eval result, or a pass rate below the base result. With no base
    result, the gate passes with a notice.
    """
    head_entries = source.blobs(head)
    before = _watched_map(source.blobs(source.merge_base(base, head)))
    after = _watched_map(head_entries)
    touched = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    if not touched:
        return GateResult(True, ["eval-gate: PASS. The PR changes no watched path."])
    shown = ", ".join(touched[:3]) + (f", and {len(touched) - 3} more" if len(touched) > 3 else "")
    lines = [f"eval-gate: the PR changes watched paths: {shown}."]
    try:
        baseline = parse_result(source.read(base, RESULT_REL), "the base branch")
        if baseline is None:
            lines.append(
                f"eval-gate: PASS with a notice. The base branch has no {RESULT_REL}, so there is no baseline. "
                f"Merge an eval result to start the gate."
            )
            return GateResult(True, lines)
        result = parse_result(source.read(head, RESULT_REL), "the PR head")
    except SdlcError as exc:
        return GateResult(False, lines + [f"eval-gate: FAIL. {exc.message}"])
    if result is None:
        lines.append(f"eval-gate: FAIL. The PR has no {RESULT_REL}. Run run-evals and commit the result.")
        return GateResult(False, lines)
    expected = watch_hash(head_entries)
    if result.watch_hash != expected:
        lines.append(
            "eval-gate: FAIL. The eval result is stale. Its watched-path hash does not match the PR head. "
            "Run run-evals again after the last change and commit the result."
        )
        return GateResult(False, lines)
    if result.rate < baseline.rate:
        lines.append(
            f"eval-gate: FAIL. The pass rate {_rate_text(result)} is below the base branch baseline {_rate_text(baseline)}."
        )
        return GateResult(False, lines)
    lines.append(f"eval-gate: PASS. The pass rate {_rate_text(result)} is not below the baseline {_rate_text(baseline)}.")
    return GateResult(True, lines)


def event_source(event_path: str | os.PathLike, slug: str):
    """Return (ApiSource, base sha, head sha) from a GitHub pull_request event file, or None."""
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SdlcError("The GitHub event file cannot be read.") from None
    pr = event.get("pull_request") if isinstance(event, dict) else None
    if not isinstance(pr, dict):
        return None
    return ApiSource(slug), pr["base"]["sha"], pr["head"]["sha"]


# Tasks and run-evals -------------------------------------------------------------


@dataclass
class Task:
    name: str
    role: str
    check: str
    prompt: str = ""
    inputs: list[str] = field(default_factory=list)
    setup: str = ""


def load_tasks(repo: Path, names: list[str] | None = None) -> list[Task]:
    """Load sdlc/evals/tasks/*.toml, sorted by name. names limits the list."""
    folder = safe_join(repo, TASKS_REL)
    paths = sorted(folder.glob("*.toml")) if folder.is_dir() else []
    tasks = []
    for path in paths:
        data = common.load_toml(path)
        where = f"{TASKS_REL}/{path.name}"
        unknown = set(data) - TASK_KEYS
        if unknown:
            raise SdlcError(f"The eval task {where} has unknown keys: {', '.join(sorted(unknown))}.")
        for key in ("role", "check"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise SdlcError(f"The eval task {where} needs {key} as text.")
        for key in ("prompt", "setup"):
            if not isinstance(data.get(key, ""), str):
                raise SdlcError(f"The eval task {where} needs {key} as text.")
        inputs = data.get("inputs", [])
        if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
            raise SdlcError(f"The eval task {where} needs inputs as a list of paths.")
        for item in inputs:
            safe_join(repo, item)
        tasks.append(Task(path.stem, data["role"], data["check"], data.get("prompt", ""), inputs, data.get("setup", "")))
    if names:
        missing = set(names) - {t.name for t in tasks}
        if missing:
            raise SdlcError(f"No eval task has the name: {', '.join(sorted(missing))}.")
        tasks = [t for t in tasks if t.name in names]
    if not tasks:
        raise SdlcError(f"No eval tasks found in {TASKS_REL}/. Add a task file first.")
    return tasks


def _shell(command: str, cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(command, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def _dirty_watched(repo: Path) -> list[str]:
    out = GitSource(repo)._git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths = []
    records = out.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index].decode("utf-8", errors="surrogateescape")
        index += 1
        if not record:
            continue
        status, path = record[:2], record[3:]
        if status[0] in "RC":
            index += 1  # the next record is the old path
        if is_watched(path):
            paths.append(path)
    return paths


def _run_task(task: Task, repo: Path, stage_dir: Path, harness: str, head: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="opieworks-sdlc-eval-"))
    worktree = tmp / "worktree"
    git = GitSource(repo)
    record = {"name": task.name, "role": task.role, "passed": False, "setup_log": "", "role_exit": None,
              "role_log": "", "check_exit": None, "check_log": "", "seconds": 0.0}
    start = time.monotonic()
    try:
        git._git("worktree", "add", "--detach", str(worktree), head)
        if task.setup:
            code, log = _shell(task.setup, worktree)
            record["setup_log"] = log
            if code != 0:
                record["setup_log"] += f"\nThe setup command stopped with exit code {code}.\n"
                return record
        config = common.load_config(worktree)
        spec = role.parse_role_spec(overlay.resolve(f"agents/{task.role}.md", stage_dir, worktree))
        argv, env = role.build_invocation(spec, harness, config, inputs=task.inputs, task=task.prompt or None)
        try:
            proc = subprocess.run(argv, env=env, cwd=worktree, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            raise SdlcError(f"The command {argv[0]} was not found. Install it or fix [agent_runner] in the config.") from None
        finally:
            role.cleanup(env)
        record["role_exit"] = proc.returncode
        record["role_log"] = proc.stdout.decode("utf-8", errors="replace")
        output_file = tmp / "role-output.txt"
        output_file.write_text(record["role_log"], encoding="utf-8")
        check_env = dict(os.environ, **{OUTPUT_ENV: str(output_file), ROLE_EXIT_ENV: str(proc.returncode)})
        code, log = _shell(task.check, worktree, env=check_env)
        record["check_exit"] = code
        record["check_log"] = log
        record["passed"] = proc.returncode == 0 and code == 0
        return record
    finally:
        record["seconds"] = round(time.monotonic() - start, 1)
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def run_evals(repo: Path, stage_dir: Path, harness: str, names: list[str] | None = None,
              now: datetime.datetime | None = None) -> tuple[dict, Path]:
    """Run the eval tasks on the HEAD commit. Write latest.json. Return (result, path).

    The watched paths must have no uncommitted change, so that the result
    belongs to the commit that the hash names.
    """
    repo = Path(repo)
    dirty = _dirty_watched(repo)
    if dirty:
        raise SdlcError(
            f"Commit the watched paths before run-evals. Uncommitted: {', '.join(dirty[:3])}"
            + (f", and {len(dirty) - 3} more." if len(dirty) > 3 else ".")
        )
    git = GitSource(repo)
    head = git._git("rev-parse", "HEAD").decode("ascii").strip()
    digest = watch_hash(git.blobs(head))
    tasks = load_tasks(repo, names)
    records = [_run_task(task, repo, Path(stage_dir), harness, head) for task in tasks]
    passed = sum(1 for r in records if r["passed"])
    when = (now or datetime.datetime.now(datetime.timezone.utc)).replace(microsecond=0)
    result = {
        "format": RESULT_FORMAT,
        "harness": harness,
        "head": head,
        "watch_hash": digest,
        "created": when.isoformat(),
        "tasks_total": len(records),
        "tasks_passed": passed,
        "pass_rate": round(passed / len(records), 4),
        "tasks": records,
    }
    dest = safe_write_path(repo, RESULT_REL)
    write_if_changed(dest, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result, dest
