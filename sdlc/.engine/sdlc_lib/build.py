"""The build stage: plan.md, the build check, and the test-lock. Owner: the build stage.

FR-40: a read-only planner role writes the plan. The engine makes the
plan.md skeleton and checks it. No code change starts before acceptance.
FR-41: the fast lane commits a failing test first. The test-lock in the
sdlc-checks job fails a PR when a later commit changes a locked test. The
lock set is the union across all PR commits.
FR-42: a changed file that plan.md does not name is drift. The build check
reports it until plan.md names the file.
FR-11: a fast-lane breach (an API, token, or visual change) stops the
build and names the design stage.

The test-lock reads the PR head only as data: git trees and blobs, never a
checkout and never a run (FR-93).
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from . import common, overlay, verify, workitem
from .common import WORK_REL, SdlcError, parse_frontmatter, safe_join, safe_write_path, write_if_changed

CONFIG_TABLE = "build"
TEST_KEY = "test"
BREACH_PATH_KEYS = {"api": "api_paths", "token": "token_paths", "visual": "visual_paths"}
PLAN_NAME = "plan.md"
PLAN_TEMPLATE = "templates/plan.md"
DEFAULT_FALLBACK = "user request"  # FR-44
UPSTREAM_NAMES = ("intent.md", "spec.md")
PLANNER_PENDING = "PENDING: the planner output goes here."
ACCEPTANCE_PENDING = "PENDING: the acceptance record goes here."
STATUSES = ("draft", "accepted")
BREACH_KINDS = ("none", "api", "token", "visual")
PHASES = ("plan", "test", "code")
BREACH_EXIT = 4
DESIGN_SKILL = "opieworks-sdlc-design"

_PLAN_PATH = re.compile(r"^sdlc/work/([0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*)/plan\.md$")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


# plan.md -------------------------------------------------------------------------


@dataclass
class Plan:
    """The machine fields of plan.md."""

    work_item: str
    lane: str
    status: str
    breach: str
    breach_reason: str
    locked_tests: list[str]
    body: str


def _text(meta: dict, key: str, where: str) -> str:
    value = meta.get(key, "")
    if not isinstance(value, str):
        raise SdlcError(f"{where} has a {key} value that is not text.")
    return value.strip()


def check_locked_path(path: str, where: str) -> str:
    """Return path when it is a safe repo-relative POSIX path. Else raise SdlcError."""
    if not isinstance(path, str) or not path.strip():
        raise SdlcError(f"{where} has an empty locked test path.")
    try:
        safe_join(Path("repo"), path)
    except SdlcError:
        raise SdlcError(f"{where} has the locked test path {path!r}, which is not a safe repo path.") from None
    if path.endswith("/") or PurePath(path).as_posix() != path:
        raise SdlcError(f"{where} has the locked test path {path!r}. Name one file with a plain relative path.")
    return path


def parse_plan(text: str, where: str = "plan.md") -> Plan:
    """Parse plan.md. Raise SdlcError on a missing or bad machine field."""
    try:
        meta, body = parse_frontmatter(text)
    except SdlcError as exc:
        raise SdlcError(f"{where} has bad frontmatter. {exc.message}") from None
    if not meta:
        raise SdlcError(f"{where} has no frontmatter.")
    work_item = _text(meta, "work_item", where)
    stage = _text(meta, "stage", where)
    if stage != "build":
        raise SdlcError(f"{where} needs stage: build.")
    lane = _text(meta, "lane", where)
    if lane not in workitem.LANES:
        raise SdlcError(f"{where} has the lane {lane!r}. Use one of: {', '.join(workitem.LANES)}.")
    status = _text(meta, "status", where)
    if status not in STATUSES:
        raise SdlcError(f"{where} has the status {status!r}. Use one of: {', '.join(STATUSES)}.")
    breach = _text(meta, "breach", where)
    if breach not in BREACH_KINDS:
        raise SdlcError(f"{where} has the breach {breach!r}. Use one of: {', '.join(BREACH_KINDS)}.")
    reason = _text(meta, "breach_reason", where)
    locked = meta.get("locked_tests", [])
    if locked == "":
        locked = []
    if not isinstance(locked, list) or not all(isinstance(item, str) for item in locked):
        raise SdlcError(f"{where} needs locked_tests as a list of paths.")
    locked = [check_locked_path(item, where) for item in locked]
    return Plan(work_item, lane, status, breach, reason, locked, body)


def plan_rel(item_id: str) -> str:
    return f"{WORK_REL}/{verify.check_item_id(item_id)}/{PLAN_NAME}"


def _item_lane(item_dir: Path) -> str | None:
    """Return the lane from item.toml or intent.md, else None."""
    item_file = item_dir / workitem.ITEM_FILE
    if item_file.is_file():
        lane = common.load_toml(item_file).get("lane")
        if isinstance(lane, str) and lane:
            return lane
    intent = item_dir / "intent.md"
    if intent.is_file():
        meta, _ = parse_frontmatter(intent.read_text(encoding="utf-8"))
        lane = meta.get("lane")
        if isinstance(lane, str) and lane:
            return lane
    return None


def _fill(template: str, values: dict[str, str]) -> str:
    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise SdlcError(f"The plan template has the unknown placeholder {{{{{name}}}}}.")
        return values[name]

    return _PLACEHOLDER.sub(fill, template)


def init_plan(item_id: str, repo: Path, stage_dir: Path, lane: str | None = None, links: list[str] | None = None,
              fallback: str | None = None, request: str | None = None) -> Path:
    """Write the plan.md skeleton for the planner. Refuse when plan.md exists.

    The lane comes from the argument, else item.toml, else intent.md. With no
    upstream artifact, the request text is the fallback input (FR-44).
    """
    dest = safe_write_path(Path(repo), plan_rel(item_id))
    if dest.exists():
        raise SdlcError(f"{plan_rel(item_id)} exists. Edit it, or delete it to start again.")
    lane = lane or _item_lane(dest.parent)
    if lane is None:
        raise SdlcError("The work item has no lane. Give --lane fast or --lane full. The plan gate of intent sets it.")
    if lane not in workitem.LANES:
        raise SdlcError(f"The lane {lane!r} is not known. Use one of: {', '.join(workitem.LANES)}.")
    found = list(links or []) or [name for name in UPSTREAM_NAMES if (dest.parent / name).is_file()]
    request = (request or "").strip()
    if not found and not request:
        raise SdlcError("The work item has no intent.md or spec.md. Give the user request with --request (FR-44).")
    upstream = verify.upstream_value(dest.parent, found, fallback or DEFAULT_FALLBACK)
    template = overlay.resolve(PLAN_TEMPLATE, Path(stage_dir), Path(repo)).read_text(encoding="utf-8")
    values = {
        "work_item": item_id,
        "upstream": upstream.rstrip("\n"),
        "lane": lane,
        "request": request or "See the upstream artifacts.",
        "planner_pending": PLANNER_PENDING,
        "acceptance_pending": ACCEPTANCE_PENDING,
    }
    write_if_changed(dest, _fill(template, values))
    return dest


# The build check -----------------------------------------------------------------


@dataclass
class CheckResult:
    code: int
    lines: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise SdlcError(f"git {args[0]} failed: {detail[-1] if detail else 'no output'}")
    return result


def default_base(repo: Path) -> str:
    """Return origin/HEAD when it is set, else main."""
    found = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else "main"


def changed_paths(repo: Path, base: str) -> list[str]:
    """Return the paths that differ from the merge base with base: commits, the work tree, and new files."""
    merge_base = _git(repo, "merge-base", base, "HEAD").stdout.strip()
    tracked = _git(repo, "diff", "--name-only", "-z", "--no-renames", merge_base).stdout.split("\0")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
    return sorted({path for path in tracked + untracked if path})


def breach_paths(config: dict) -> dict[str, list[str]]:
    """Return {kind: globs} from the [build] config table. Missing keys give empty lists."""
    table = config.get(CONFIG_TABLE)
    table = table if isinstance(table, dict) else {}
    found = {}
    for kind, key in BREACH_PATH_KEYS.items():
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SdlcError(f"The config key {CONFIG_TABLE}.{key} must be a list of path patterns.")
        found[kind] = value
    return found


def breach_message(item_id: str, kind: str, reason: str) -> list[str]:
    """Return the stop message for a fast-lane breach (FR-11, SCN-4)."""
    what = {"api": "an API change", "token": "a design token change", "visual": "a visual change"}[kind]
    return [
        f"build-check: STOP. The fast-lane work item {item_id} needs {what}: {(reason or 'no reason given').rstrip('.')}.",
        "build-check: The fast lane allows no API, token, or visual change (FR-11). Push nothing.",
        f"build-check: Route the work item to the design stage ({DESIGN_SKILL}). The gate owner moves it to "
        "the full lane. Then plan it again.",
    ]


def _load_plan(repo: Path, item_id: str) -> tuple[Plan, str]:
    path = safe_join(repo, plan_rel(item_id))
    if not path.is_file():
        raise SdlcError(f"{plan_rel(item_id)} does not exist. Run plan-init first.")
    text = path.read_text(encoding="utf-8")
    plan = parse_plan(text, plan_rel(item_id))
    if plan.work_item != item_id:
        raise SdlcError(f"{plan_rel(item_id)} has work_item {plan.work_item!r}, not {item_id!r}.")
    return plan, text


def build_check(item_id: str, repo: Path, phase: str, config: dict, base: str | None = None,
                runner=None) -> CheckResult:
    """Check a work item before the next build step.

    plan: plan.md is complete. A fast-lane breach stops the build (exit 4).
    test: the fast lane locked tests exist, and the test command fails.
    code: the plan is accepted, no fast-lane breach path changed, and plan.md
    names every changed file (FR-42).
    """
    if phase not in PHASES:
        raise SdlcError(f"The phase {phase!r} is not known. Use one of: {', '.join(PHASES)}.")
    repo = Path(repo)
    plan, text = _load_plan(repo, item_id)
    rel = plan_rel(item_id)
    problems: list[str] = []
    if plan.breach != "none" and not plan.breach_reason:
        problems.append(f"{rel} names the breach {plan.breach} but gives no breach_reason.")
    if plan.lane == "fast" and plan.breach != "none":
        return CheckResult(BREACH_EXIT, breach_message(item_id, plan.breach, plan.breach_reason))
    if PLANNER_PENDING in text:
        problems.append(f"{rel} still has the line {PLANNER_PENDING!r}. Paste the planner output there.")
    if plan.lane == "fast" and not plan.locked_tests:
        problems.append(f"{rel} is in the fast lane and has no locked_tests. Name the failing test to write first (FR-41).")
    if phase in ("test", "code") and plan.lane == "fast":
        for path in plan.locked_tests:
            if not safe_join(repo, path).is_file():
                problems.append(f"The locked test {path} does not exist. Write it first.")
    notes: list[str] = []
    if phase == "test" and not problems:
        found, notes = _failing_test(repo, config, runner)
        problems.extend(found)
    if phase == "code":
        problems.extend(_accepted(repo, item_id, plan, text, base or default_base(repo)))
        changed = changed_paths(repo, base or default_base(repo))
        if plan.lane == "fast":
            for kind, globs in breach_paths(config).items():
                hits = [p for p in changed if any(fnmatch.fnmatchcase(p, g) for g in globs)]
                if hits:
                    reason = f"the change touches {', '.join(hits[:3])}, which the config key {CONFIG_TABLE}.{BREACH_PATH_KEYS[kind]} names"
                    return CheckResult(BREACH_EXIT, breach_message(item_id, kind, reason))
        for path in changed:
            if not path.startswith(WORK_REL + "/") and not names_path(text, path):
                problems.append(f"Drift: {path} changed, but {rel} does not name it. Add it to the plan or the drift log (FR-42).")
    if problems:
        return CheckResult(1, [f"build-check: FAIL. {line}" for line in problems])
    return CheckResult(0, notes + [f"build-check: PASS. The {phase} phase of {item_id} ({plan.lane} lane) is ready."])


def names_path(text: str, path: str) -> bool:
    """Return True when text names path as a whole path, not as a part of a longer path."""
    return re.search(r"(?<![\w./-])" + re.escape(path) + r"(?![\w/-])", text) is not None


def _accepted(repo: Path, item_id: str, plan: Plan, text: str, base: str) -> list[str]:
    """Return problems when the plan is not accepted (FR-40, D17)."""
    rel = plan_rel(item_id)
    if plan.lane == "fast":
        problems = []
        if plan.status != "accepted":
            problems.append(f"{rel} has status {plan.status}. The human accepts the fast-lane plan in the session first (D17).")
        if ACCEPTANCE_PENDING in text:
            problems.append(f"{rel} still has the line {ACCEPTANCE_PENDING!r}. Record who accepted the plan and when.")
        return problems
    found = _git(repo, "cat-file", "-e", f"{base}:{rel}", check=False)
    if found.returncode != 0:
        return [f"{rel} is not on {base}. In the full lane, the gate owner merges the plan PR before the code starts (D17)."]
    return []


def test_command(config: dict) -> str:
    table = config.get(CONFIG_TABLE)
    table = table if isinstance(table, dict) else {}
    command = table.get(TEST_KEY, "")
    if not isinstance(command, str):
        raise SdlcError(f"The config key {CONFIG_TABLE}.{TEST_KEY} must be a string.")
    return command.strip()


def _failing_test(repo: Path, config: dict, runner=None) -> tuple[list[str], list[str]]:
    """Run the test command. The fast lane needs it to fail before the fix (FR-41).

    Return (problems, notes). The notes hold the exit code and the last
    output lines, verbatim.
    """
    command = test_command(config)
    if not command:
        return [f"The config key {CONFIG_TABLE}.{TEST_KEY} is empty. Ask the human for the command that runs the tests."], []
    code, output = (runner or verify._run_once)(command, repo)
    tail = "\n".join(output.rstrip("\n").splitlines()[-20:])
    if code == 0:
        return [f"The test command `{command}` passed. A locked test must fail before the fix. Output:\n{tail}"], []
    return [], [f"build-check: The test command `{command}` failed with exit code {code}, as it must before the fix. "
                f"Last lines (raw output):\n{tail}"]


# The test-lock (FR-41) -----------------------------------------------------------


@dataclass(frozen=True)
class Lock:
    path: str
    entry: tuple[str, str] | None  # (mode, blob sha)
    commit: str
    item: str


def _tree(source, rev: str) -> dict[str, tuple[str, str]]:
    return {path: (mode, sha) for mode, path, sha in source.blobs(rev)}


def pr_commits(source, base: str, head: str) -> list[tuple[str, list[str]]]:
    """Return the PR commits as (sha, parent shas), parents before children.

    The list holds the commits that head has and the merge base does not.
    """
    getter = getattr(source, "commits", None)
    if getter is not None:
        found = getter(base, head)
    elif hasattr(source, "_git"):
        merge_base = source.merge_base(base, head)
        out = source._git("rev-list", "--topo-order", "--parents", f"{merge_base}..{head}").decode("ascii")
        found = [(parts[0], parts[1:]) for parts in (line.split() for line in out.splitlines()) if parts]
    else:
        data = source._api(f"compare/{base}...{head}")
        commits = data.get("commits", [])
        if data.get("total_commits", len(commits)) > len(commits):
            raise SdlcError("The PR has more commits than the GitHub compare API returns. The test-lock cannot read them all.")
        found = [(c["sha"], [p["sha"] for p in c.get("parents", [])]) for c in commits]
    return _parents_first(found)


def _parents_first(commits: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    shas = {sha for sha, _ in commits}
    parents = {sha: [p for p in ps if p in shas] for sha, ps in commits}
    done: set[str] = set()
    ordered = []
    pending = [sha for sha, _ in commits]
    while pending:
        progress = False
        for sha in list(pending):
            if all(p in done for p in parents[sha]):
                ordered.append((sha, parents[sha]))
                done.add(sha)
                pending.remove(sha)
                progress = True
        if not progress:
            raise SdlcError("The PR commits have a parent loop. The test-lock cannot order them.")
    return ordered


def _short(sha: str) -> str:
    return sha[:7]


def test_lock(source, base: str, head: str):
    """Run the test-lock for a PR from base to head (FR-41).

    A plan.md that the PR adds or changes locks each path in its
    locked_tests from the first commit that has both the entry and the file.
    A lock stays for every later commit, also when a later commit removes the
    entry or the plan (the union across all PR commits). A later commit that
    changes, deletes, or changes the mode of a locked file fails the check. In
    the fast lane, each lock must start in the first commit that changes the
    plan, so the failing test comes first.
    """
    from .evals import GateResult

    try:
        return GateResult(*_test_lock(source, base, head))
    except SdlcError as exc:
        return GateResult(False, [f"test-lock: FAIL. {exc.message}"])


def _test_lock(source, base: str, head: str) -> tuple[bool, list[str]]:
    merge_base = source.merge_base(base, head)
    base_tree = _tree(source, merge_base)
    commits = pr_commits(source, base, head)
    if not commits:
        return True, ["test-lock: PASS. The PR has no commits."]
    plans: dict[tuple[str, str], Plan] = {}
    plan_start: dict[str, str] = {}
    locks_at: dict[str, dict[str, Lock]] = {}
    declared: dict[str, str] = {}  # path -> work item, for entries with no file yet
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for sha, parents in commits:
        tree = _tree(source, sha)
        locks: dict[str, Lock] = {}
        for parent in parents:
            for path, lock in locks_at.get(parent, {}).items():
                locks.setdefault(path, lock)
        for path, lock in locks.items():
            now = tree.get(path)
            if now != lock.entry and (path, lock.commit) not in seen:
                seen.add((path, lock.commit))
                verb = "deletes" if now is None else "changes"
                problems.append(
                    f"Commit {_short(sha)} {verb} the locked test {path}. Commit {_short(lock.commit)} locked it "
                    f"for work item {lock.item}. Do not change a locked test. Revert the commit, or close the PR and plan again."
                )
        for path, entry in sorted(tree.items()):
            match = _PLAN_PATH.match(path)
            if not match or base_tree.get(path) == entry:
                continue
            plan_start.setdefault(path, sha)
            key = (path, entry[1])
            if key not in plans:
                plans[key] = parse_plan(_read(source, sha, path), f"{path} in commit {_short(sha)}")
            plan = plans[key]
            for test in plan.locked_tests:
                if test in locks:
                    continue
                if test not in tree:
                    declared.setdefault(test, match.group(1))
                    continue
                if plan.lane == "fast" and sha != plan_start[path]:
                    problems.append(
                        f"Fast lane: commit {_short(sha)} locks {test}, but the first commit of {path} is "
                        f"{_short(plan_start[path])}. Commit the failing test and the plan first (FR-41)."
                    )
                locks[test] = Lock(test, tree[test], sha, match.group(1))
        locks_at[sha] = locks
    final = locks_at[commits[-1][0]]
    for test, item in sorted(declared.items()):
        if test not in final:
            problems.append(f"The plan of work item {item} locks {test}, but no PR commit has that file.")
    head_tree = _tree(source, commits[-1][0])
    for path, entry in sorted(head_tree.items()):
        match = _PLAN_PATH.match(path)
        if match and base_tree.get(path) != entry:
            plan = plans[(path, entry[1])]
            if plan.lane == "fast" and not plan.locked_tests and not any(l.item == match.group(1) for l in final.values()):
                problems.append(f"{path} is in the fast lane and locks no test. Commit a failing test first (FR-41).")
    if problems:
        return False, [f"test-lock: FAIL. {line}" for line in problems]
    if not final:
        return True, ["test-lock: PASS. The PR locks no test."]
    names = ", ".join(sorted(final))
    return True, [f"test-lock: PASS. {len(final)} locked tests stay unchanged in {len(commits)} commits: {names}."]


def _read(source, rev: str, path: str) -> str:
    data = source.read(rev, path)
    if data is None:
        raise SdlcError(f"The commit {_short(rev)} has no {path}.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise SdlcError(f"{path} in commit {_short(rev)} is not UTF-8 text.") from None
