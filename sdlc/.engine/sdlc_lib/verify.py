"""The verify loop and evidence.md (FR-50, FR-54, NFR-7). Owner: the testing stage.

The engine runs the one verify command from the config. It runs the
command again after a failure, up to the run limit, and stops at the first
pass. It writes the raw output of every run into evidence.md. A model never
writes or changes the raw output.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import overlay
from .common import WORK_REL, SdlcError, safe_join, safe_write_path, write_if_changed

CONFIG_TABLE = "testing"
VERIFY_KEY = "verify"
MAX_RUNS_KEY = "max_runs"
EVIDENCE_NAME = "evidence.md"
EVIDENCE_TEMPLATE = "templates/evidence.md"
DEFAULT_FALLBACK = "PR description and diff"
# Artifacts of earlier stages. evidence.md links to the ones that exist (FR-6).
UPSTREAM_NAMES = ("intent.md", "spec.md", "plan.md")
REPORT_PENDING = "PENDING: the verifier report goes here."

_ITEM_ID = re.compile(r"^[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*$")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


@dataclass
class Attempt:
    """One run of the verify command."""

    number: int
    exit_code: int
    output: str
    seconds: float


@dataclass
class VerifyResult:
    """The result of the verify loop."""

    command: str
    max_runs: int
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].exit_code == 0

    @property
    def verdict(self) -> str:
        """PASS, PASS AFTER RETRY (a flaky result), or FAIL."""
        if not self.passed:
            return "FAIL"
        return "PASS" if len(self.attempts) == 1 else "PASS AFTER RETRY"


def settings(config: dict) -> tuple[str, int]:
    """Return (verify command, run limit) from the [testing] config table."""
    table = config.get(CONFIG_TABLE)
    table = table if isinstance(table, dict) else {}
    command = table.get(VERIFY_KEY, "")
    if not isinstance(command, str):
        raise SdlcError(f"The config key {CONFIG_TABLE}.{VERIFY_KEY} must be a string.")
    max_runs = table.get(MAX_RUNS_KEY)
    if max_runs is None:
        raise SdlcError(
            f"The config has no {CONFIG_TABLE}.{MAX_RUNS_KEY}. Add it to sdlc/config.toml, for example {MAX_RUNS_KEY} = 5."
        )
    if isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs < 1:
        raise SdlcError(f"The config key {CONFIG_TABLE}.{MAX_RUNS_KEY} must be a whole number of 1 or more.")
    return command.strip(), max_runs


def _run_once(command: str, repo: Path) -> tuple[int, str]:
    """Run the command in a shell in repo. Return (exit code, stdout and stderr together)."""
    try:
        result = subprocess.run(
            command, shell=True, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, f"The shell cannot start the command: {exc}\n"
    # Keep every byte that is valid UTF-8. Bytes that are not valid UTF-8
    # become U+FFFD, so evidence.md stays valid text.
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def run_verify(command: str, repo: Path, max_runs: int, runner=None) -> VerifyResult:
    """Run command up to max_runs times. Stop at the first pass (exit code 0).

    runner(command, repo) -> (exit code, output) replaces the shell in tests.
    """
    if not command:
        raise SdlcError(
            f"The config key {CONFIG_TABLE}.{VERIFY_KEY} is empty. Run the propose-verify command, "
            f"then write the verify command in sdlc/config.toml."
        )
    if max_runs < 1:
        raise SdlcError("The run limit must be 1 or more.")
    runner = runner or _run_once
    result = VerifyResult(command=command, max_runs=max_runs)
    for number in range(1, max_runs + 1):
        start = time.monotonic()
        code, output = runner(command, Path(repo))
        result.attempts.append(Attempt(number, code, output, round(time.monotonic() - start, 1)))
        if code == 0:
            break
    return result


def check_item_id(item_id: str) -> str:
    if not isinstance(item_id, str) or not _ITEM_ID.match(item_id):
        raise SdlcError(f"The work item ID {item_id!r} is not valid. The format is YYYYMMDD-<slug>.")
    return item_id


def upstream_value(item_dir: Path, links: list[str] | None, fallback: str | None) -> str:
    """Return the upstream frontmatter lines (FR-3, FR-6).

    Explicit links win. Else the artifacts of earlier stages in the work item
    folder. Else "none, used <fallback>".
    """
    found = list(links or [])
    if not found:
        found = [name for name in UPSTREAM_NAMES if (item_dir / name).is_file()]
    if found:
        return "upstream:\n" + "".join(f"  - {json.dumps(link)}\n" for link in found)
    text = (fallback or DEFAULT_FALLBACK).strip()
    if not text or "\n" in text:
        raise SdlcError("The fallback text must be one line.")
    return f"upstream: {json.dumps('none, used ' + text)}\n"


def _fence(text: str) -> str:
    """Return a backtick fence that is longer than every backtick run in text."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_runs(result: VerifyResult) -> str:
    """Return the markdown for every run, with the raw output in a fenced block."""
    parts = []
    for attempt in result.attempts:
        output = attempt.output if attempt.output.endswith("\n") or not attempt.output else attempt.output + "\n"
        fence = _fence(output)
        parts.append(
            f"### Run {attempt.number} of {result.max_runs}: exit code {attempt.exit_code}, {attempt.seconds} s\n\n"
            f"{fence}text\n{output}{fence}\n"
        )
    return "\n".join(parts)


def render_evidence(template: str, values: dict[str, str]) -> str:
    """Fill {{name}} placeholders in the template. An unknown name raises SdlcError."""

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise SdlcError(f"The evidence template has the unknown placeholder {{{{{name}}}}}.")
        return values[name]

    return _PLACEHOLDER.sub(fill, template)


def write_evidence(item_id: str, repo: Path, stage_dir: Path, result: VerifyResult,
                   links: list[str] | None = None, fallback: str | None = None) -> Path:
    """Write sdlc/work/<id>/evidence.md. Make the work item folder when it is absent."""
    check_item_id(item_id)
    rel = f"{WORK_REL}/{item_id}/{EVIDENCE_NAME}"
    dest = safe_write_path(repo, rel)
    template = overlay.resolve(EVIDENCE_TEMPLATE, stage_dir, repo).read_text(encoding="utf-8")
    values = {
        "work_item": item_id,
        "upstream": upstream_value(dest.parent, links, fallback).rstrip("\n"),
        "verify_command": result.command,
        "max_runs": str(result.max_runs),
        "run_count": str(len(result.attempts)),
        "verdict": result.verdict,
        "runs": render_runs(result).rstrip("\n"),
        "report_pending": REPORT_PENDING,
    }
    write_if_changed(dest, render_evidence(template, values))
    return dest


# Proposals for a bare repo (FR-54) -----------------------------------------------


def _package_manager(repo: Path) -> str:
    for lock, tool in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if (repo / lock).is_file():
            return tool
    return "npm"


def _make_targets(text: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", text, flags=re.MULTILINE))


def propose(repo: Path) -> list[tuple[str, str]]:
    """Return verify command proposals as (command, reason), best first.

    The proposal reads only files in the repo. It runs nothing.
    """
    repo = Path(repo)
    found: list[tuple[str, str]] = []
    package = repo / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
        except (ValueError, UnicodeDecodeError, AttributeError):
            scripts = {}
        tool = _package_manager(repo)
        for name in ("verify", "check", "test"):
            if isinstance(scripts, dict) and name in scripts:
                command = f"{tool} test" if name == "test" and tool != "bun" else f"{tool} run {name}"
                found.append((command, f"package.json has a {name!r} script"))
    makefile = repo / "Makefile"
    if makefile.is_file():
        try:
            targets = _make_targets(makefile.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            targets = set()
        for name in ("verify", "check", "test"):
            if name in targets:
                found.append((f"make {name}", f"the Makefile has a {name!r} target"))
    if any((repo / name).is_file() for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")) or (repo / "tests").is_dir():
        found.append(("python3 -m pytest", "the repo has Python project files or a tests folder"))
    if (repo / "go.mod").is_file():
        found.append(("go test ./...", "the repo has go.mod"))
    if (repo / "Cargo.toml").is_file():
        found.append(("cargo test", "the repo has Cargo.toml"))
    return found
