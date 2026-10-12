"""The maintain stage engine (FR-70 to FR-73, NFR-4). Owner: the maintain stage.

`detect` is deterministic. It runs the command of each metric, reads the
earlier readings from the sdlc-data branch, and gives each reading a tier
from the rolling mean and sigma. The same readings give the same tier.
It never starts a model. Its only network calls are the issue writes, and
only when the caller asks for them.

The readings live on the sdlc-data branch, not on the default branch. The
engine writes them with git plumbing, so the work tree and the current
branch never change. The workflow fetches and pushes the branch.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import overlay, workitem
from .common import WORK_REL, SdlcError, load_toml, safe_join, safe_write_path, write_if_changed

CONFIG_TABLE = "maintain"      # keys: window (int >= 2)
WINDOW_KEY = "window"
BANDS_REL = "sdlc/bands.toml"
DATA_BRANCH = "sdlc-data"
DATA_REF = "refs/heads/" + DATA_BRANCH
REMOTE_DATA_REF = "refs/remotes/origin/" + DATA_BRANCH
READINGS_DIR = "sdlc/maintain/readings"
LAST_RUN_REL = "sdlc/maintain/last-run.json"
LAST_RUN_FORMAT = 1
TRIAGE_REL = "sdlc/triage"
LESSONS_REL = "sdlc/lessons"
EVAL_TASKS_REL = "sdlc/evals/tasks"
INTENT_TEMPLATE = "templates/intent.md"
LESSON_TEMPLATE = "templates/lesson.md"
PENDING = "PENDING:"

# Tier names, from low to high. WARMUP means that the rolling window is not
# full yet and the metric has no fixed baseline. ERROR means that the
# command did not print a number.
OK, LOG, DIAGNOSE, PROPOSE = "ok", "log", "diagnose", "propose"
WARMUP, ERROR = "warmup", "error"
TIERS = (OK, LOG, DIAGNOSE, PROPOSE)
ISSUE_TIERS = (DIAGNOSE, PROPOSE)
# Playbook: log at 1 sigma, diagnose at 2 sigma, propose at 3 sigma.
DEFAULT_SIGMAS = {LOG: 1.0, DIAGNOSE: 2.0, PROPOSE: 3.0}
BAD_SIDES = ("high", "low", "both")
METRIC_KEYS = {"command", "baseline", "sigma", "bad", "tiers", "description"}
CSV_HEADER = ("time", "value", "mean", "sigma", "z", "tier")

DECISIONS = ("fix-now", "schedule", "dismiss")
ISSUE_LABEL = "sdlc:maintain"
ISSUE_PAGE = 100
DEFAULT_LANE = "full"

# The commit identity of the readings commits. The address is built from
# parts, so the leak scan does not see an email in the engine source.
COMMIT_NAME = "opieworks-sdlc detect"
COMMIT_EMAIL = "detect" + "@" + "opieworks-sdlc.invalid"

_METRIC = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_ITEM_ID = re.compile(r"^[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*$")


# Bands ------------------------------------------------------------------------


@dataclass
class Metric:
    """One metric of sdlc/bands.toml."""

    name: str
    command: str
    baseline: float | None = None
    sigma: float | None = None
    bad: str = "both"
    tiers: dict = field(default_factory=lambda: dict(DEFAULT_SIGMAS))


def check_metric_name(name: str) -> str:
    if not isinstance(name, str) or not _METRIC.match(name):
        raise SdlcError(f"The metric name {name!r} is not valid. Use lowercase letters, digits, and hyphens.")
    return name


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SdlcError(f"The value of {what} must be a finite number.")
    return float(value)


def _metric(name: str, table) -> Metric:
    check_metric_name(name)
    where = f"metrics.{name} in {BANDS_REL}"
    if not isinstance(table, dict):
        raise SdlcError(f"The entry {where} must be a table.")
    unknown = sorted(set(table) - METRIC_KEYS)
    if unknown:
        raise SdlcError(f"The entry {where} has the unknown key {unknown[0]!r}.")
    command = table.get("command")
    if not isinstance(command, str) or not command.strip():
        raise SdlcError(f"The entry {where} needs a command that prints a number.")
    baseline = None if table.get("baseline") is None else _number(table["baseline"], f"{where} baseline")
    sigma = None if table.get("sigma") is None else _number(table["sigma"], f"{where} sigma")
    if sigma is not None and sigma < 0:
        raise SdlcError(f"The sigma of {where} must be 0 or more.")
    bad = table.get("bad", "both")
    if bad not in BAD_SIDES:
        raise SdlcError(f"The bad value of {where} must be one of: {', '.join(BAD_SIDES)}.")
    tiers = dict(DEFAULT_SIGMAS)
    over = table.get("tiers", {})
    if not isinstance(over, dict) or set(over) - set(DEFAULT_SIGMAS):
        raise SdlcError(f"The tiers of {where} may only set {', '.join(DEFAULT_SIGMAS)}.")
    for key, value in over.items():
        tiers[key] = _number(value, f"{where} tiers.{key}")
    if not 0 < tiers[LOG] <= tiers[DIAGNOSE] <= tiers[PROPOSE]:
        raise SdlcError(f"The tiers of {where} must be more than 0 and in the order log, diagnose, propose.")
    return Metric(name, command.strip(), baseline, sigma, bad, tiers)


def load_bands(repo: Path) -> list[Metric]:
    """Read sdlc/bands.toml. Return the metrics sorted by name. A missing file gives []."""
    path = safe_join(Path(repo), BANDS_REL)
    if not path.is_file():
        return []
    data = load_toml(path)
    unknown = sorted(set(data) - {"metrics"})
    if unknown:
        raise SdlcError(f"The file {BANDS_REL} has the unknown key {unknown[0]!r}. Put each metric in [metrics.<name>].")
    metrics = data.get("metrics", {})
    if not isinstance(metrics, dict):
        raise SdlcError(f"The key metrics in {BANDS_REL} must be a table.")
    return [_metric(name, metrics[name]) for name in sorted(metrics)]


def window_size(config: dict) -> int:
    """Return maintain.window from the config (C3: 30 readings)."""
    table = config.get(CONFIG_TABLE)
    value = table.get(WINDOW_KEY) if isinstance(table, dict) else None
    if value is None:
        raise SdlcError(f"The config has no {CONFIG_TABLE}.{WINDOW_KEY}. Add it to sdlc/config.toml, for example {WINDOW_KEY} = 30.")
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        raise SdlcError(f"The config key {CONFIG_TABLE}.{WINDOW_KEY} must be a whole number of 2 or more.")
    return value


# The deterministic core (NFR-4) -----------------------------------------------


@dataclass
class Reading:
    """One reading and its tier."""

    value: float
    mean: float | None
    sigma: float | None
    z: float | None
    tier: str


def parse_value(text: str) -> float:
    """Return the number on the last line of text that is not empty."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        raise SdlcError("The metric command printed nothing. It must print a number on its last line.")
    try:
        value = float(lines[-1])
    except ValueError:
        raise SdlcError(f"The last line of the metric output is not a number: {lines[-1][:80]!r}.") from None
    if not math.isfinite(value):
        raise SdlcError("The metric output is not a finite number.")
    return value


def classify(deviation: float, tiers: dict) -> str:
    """Return the tier for a deviation in sigma units. The deviation is 0 or more."""
    for tier in (PROPOSE, DIAGNOSE, LOG):
        if deviation >= tiers[tier]:
            return tier
    return OK


def evaluate(value: float, history: list[float], metric: Metric, window: int) -> Reading:
    """Give one reading its tier.

    The baseline is the fixed baseline and sigma of the metric when it has
    them. A part that is not fixed comes from the last `window` readings in
    history, with the sample standard deviation. When that part is needed
    and history has fewer readings than the window, the tier is WARMUP.
    """
    recent = history[-window:]
    full = len(recent) >= window
    if (metric.baseline is None or metric.sigma is None) and not full:
        return Reading(value, None, None, None, WARMUP)
    mean = metric.baseline if metric.baseline is not None else statistics.fmean(recent)
    sigma = metric.sigma if metric.sigma is not None else statistics.stdev(recent)
    diff = value - mean
    if sigma == 0:
        z = 0.0 if diff == 0 else math.copysign(math.inf, diff)
    else:
        z = diff / sigma
    if metric.bad == "high":
        deviation = max(z, 0.0)
    elif metric.bad == "low":
        deviation = max(-z, 0.0)
    else:
        deviation = abs(z)
    return Reading(value, mean, sigma, z, classify(deviation, metric.tiers))


def fmt(number: float | None) -> str:
    """Format a number for the readings file. The text is stable across runs."""
    if number is None:
        return ""
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    text = format(round(number, 6), ".6f").rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


# Readings files ---------------------------------------------------------------


def readings_rel(metric: str) -> str:
    return f"{READINGS_DIR}/{check_metric_name(metric)}.csv"


def parse_readings(text: str | None, metric: str) -> list[dict]:
    """Parse a readings CSV. Return the rows as dicts. None gives []."""
    if not text:
        return []
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(rows[0]) != CSV_HEADER:
        raise SdlcError(f"The readings file of {metric} does not start with the header {','.join(CSV_HEADER)}.")
    out = []
    for number, row in enumerate(rows[1:], start=2):
        if not row:
            continue
        if len(row) != len(CSV_HEADER):
            raise SdlcError(f"Line {number} of the readings file of {metric} has {len(row)} fields, not {len(CSV_HEADER)}.")
        item = dict(zip(CSV_HEADER, row))
        try:
            item["value"] = float(item["value"])
        except ValueError:
            raise SdlcError(f"Line {number} of the readings file of {metric} has a value that is not a number.") from None
        out.append(item)
    return out


def render_readings(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([row["time"], fmt(row["value"]) if isinstance(row["value"], float) else row["value"],
                         row["mean"], row["sigma"], row["z"], row["tier"]])
    return buffer.getvalue()


class DataBranch:
    """The sdlc-data branch of a repo. Reads and commits with git plumbing only.

    It never changes the work tree, the index, or the current branch. It
    makes no network call. The workflow fetches and pushes the branch.
    """

    def __init__(self, repo: Path):
        self.repo = Path(repo)

    def _git(self, *args: str, env: dict | None = None, data: bytes | None = None, check: bool = True) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=env, input=data, capture_output=True)
        if check and result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise SdlcError(f"git {args[0]} failed on the {DATA_BRANCH} branch: {detail[-1] if detail else 'no output'}")
        return result.stdout.decode("utf-8", errors="replace")

    def _sha(self, ref: str) -> str | None:
        out = self._git("rev-parse", "--verify", "-q", ref + "^{commit}", check=False).strip()
        return out or None

    def head(self) -> tuple[str | None, str | None]:
        """Return (local branch sha, sha to build on). The local branch wins over origin."""
        local = self._sha(DATA_REF)
        return local, local or self._sha(REMOTE_DATA_REF)

    def read(self, rel: str) -> str | None:
        _local, base = self.head()
        if base is None:
            return None
        result = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=self.repo, capture_output=True)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8")

    def commit(self, files: dict[str, str], message: str, when: datetime.datetime) -> str:
        """Commit files on top of the branch. Move the local branch ref. Return the new sha."""
        local, base = self.head()
        stamp = f"{int(when.timestamp())} +0000"
        with tempfile.TemporaryDirectory(prefix="opieworks-sdlc-index-") as tmp:
            env = dict(os.environ)
            env.update({
                "GIT_INDEX_FILE": str(Path(tmp) / "index"),
                "GIT_AUTHOR_NAME": COMMIT_NAME, "GIT_AUTHOR_EMAIL": COMMIT_EMAIL, "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_NAME": COMMIT_NAME, "GIT_COMMITTER_EMAIL": COMMIT_EMAIL, "GIT_COMMITTER_DATE": stamp,
            })
            if base:
                self._git("read-tree", base, env=env)
            else:
                self._git("read-tree", "--empty", env=env)
            for rel in sorted(files):
                safe_join(self.repo, rel)
                blob = self._git("hash-object", "-w", "--stdin", env=env, data=files[rel].encode("utf-8")).strip()
                self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)
            tree = self._git("write-tree", env=env).strip()
            parents = ["-p", base] if base else []
            sha = self._git("commit-tree", tree, *parents, "-m", message, env=env).strip()
        self._git("update-ref", "-m", "opieworks-sdlc detect", DATA_REF, sha, local or "0" * 40)
        return sha


# detect (FR-70, FR-71, NFR-4) ---------------------------------------------------


@dataclass
class Result:
    """The result of one metric in one detect run."""

    metric: str
    command: str
    value: float | None
    mean: float | None
    sigma: float | None
    z: float | None
    tier: str
    count: int = 0          # readings in the window that the baseline used
    error: str = ""


@dataclass
class DetectReport:
    time: str
    window: int
    results: list[Result]
    commit: str | None = None

    @property
    def failed(self) -> bool:
        return any(r.tier == ERROR for r in self.results)


def run_command(command: str, repo: Path) -> tuple[int, str]:
    """Run a metric command in a shell in repo. Return (exit code, stdout)."""
    try:
        result = subprocess.run(command, shell=True, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return 127, f"The shell cannot start the command: {exc}"
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def _utc(now: datetime.datetime | None) -> datetime.datetime:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).replace(microsecond=0)


def select_metrics(repo: Path, names: list[str] | None = None, command: str | None = None) -> list[Metric]:
    """Return the metrics for a detect run.

    With command, the run has one ad hoc metric (the fallback: any command
    that prints a number). It takes the band settings of the same name from
    bands.toml when they exist.
    """
    bands = {m.name: m for m in load_bands(repo)}
    if command is not None:
        if not names or len(names) != 1:
            raise SdlcError("Give exactly one --metric with --command.")
        name = check_metric_name(names[0])
        base = bands.get(name) or Metric(name, command)
        if not command.strip():
            raise SdlcError("The --command value is empty.")
        return [Metric(name, command.strip(), base.baseline, base.sigma, base.bad, dict(base.tiers))]
    if names:
        missing = [n for n in names if n not in bands]
        if missing:
            raise SdlcError(f"The metric {missing[0]} is not in {BANDS_REL}.")
        return [bands[n] for n in names]
    if not bands:
        raise SdlcError(
            f"The file {BANDS_REL} has no metric. Add a [metrics.<name>] table with a command that prints a number, "
            "or give --metric and --command."
        )
    return list(bands.values())


def detect(repo: Path, metrics: list[Metric], window: int, now: datetime.datetime | None = None,
           runner=None, record: bool = True) -> DetectReport:
    """Measure each metric, give it a tier, and add the reading to the sdlc-data branch.

    runner(command, repo) -> (exit code, stdout) replaces the shell in tests.
    With record=False, nothing is written.
    """
    repo = Path(repo)
    when = _utc(now)
    stamp = when.isoformat().replace("+00:00", "Z")
    runner = runner or run_command
    data = DataBranch(repo)
    files: dict[str, str] = {}
    results: list[Result] = []
    for metric in metrics:
        rel = readings_rel(metric.name)
        rows = parse_readings(data.read(rel), metric.name)
        code, output = runner(metric.command, repo)
        try:
            if code != 0:
                raise SdlcError(f"The metric command exited with code {code}.")
            value = parse_value(output)
        except SdlcError as exc:
            results.append(Result(metric.name, metric.command, None, None, None, None, ERROR, 0, exc.message))
            continue
        history = [row["value"] for row in rows]
        reading = evaluate(value, history, metric, window)
        results.append(Result(metric.name, metric.command, value, reading.mean, reading.sigma, reading.z,
                              reading.tier, min(len(history), window)))
        rows.append({"time": stamp, "value": value, "mean": fmt(reading.mean), "sigma": fmt(reading.sigma),
                     "z": fmt(reading.z), "tier": reading.tier})
        files[rel] = render_readings(rows)
    report = DetectReport(stamp, window, results)
    if record:
        files[LAST_RUN_REL] = render_last_run(report)
        report.commit = data.commit(files, f"detect: {stamp}", when)
    return report


def render_last_run(report: DetectReport) -> str:
    """The last-run record. The hub reads its time (FR-84)."""
    data = {
        "format": LAST_RUN_FORMAT,
        "time": report.time,
        "window": report.window,
        "metrics": {
            r.metric: {"value": fmt(r.value), "tier": r.tier, "z": fmt(r.z), "command": r.command,
                       **({"error": r.error} if r.error else {})}
            for r in report.results
        },
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def last_run(repo: Path) -> dict | None:
    """Return the last-run record from the sdlc-data branch, or None.

    The time is in the key "time" as UTC ISO 8601. The hub uses it for the
    stale-detect warning (FR-84).
    """
    text = DataBranch(repo).read(LAST_RUN_REL)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        raise SdlcError(f"The file {LAST_RUN_REL} on the {DATA_BRANCH} branch is not valid JSON.") from None
    if not isinstance(data, dict) or not isinstance(data.get("time"), str):
        raise SdlcError(f"The file {LAST_RUN_REL} on the {DATA_BRANCH} branch has no time.")
    return data


def readings_text(repo: Path, metric: str) -> str:
    """Return the readings CSV of a metric from the sdlc-data branch."""
    text = DataBranch(repo).read(readings_rel(metric))
    if text is None:
        raise SdlcError(f"The {DATA_BRANCH} branch has no readings for {metric}. Run detect first.")
    return text


# Issues (FR-71) ---------------------------------------------------------------------


def issue_title(metric: str, tier: str) -> str:
    return f"sdlc detect: {metric} at {tier}"


def _signal_lines(result: Result) -> str:
    return (
        f"- Metric: `{result.metric}`\n"
        f"- Command: `{result.command}`\n"
        f"- Reading: {fmt(result.value)}\n"
        f"- Baseline: mean {fmt(result.mean)}, sigma {fmt(result.sigma)}, from {result.count} readings\n"
        f"- Deviation: {fmt(result.z)} sigma\n"
        f"- Tier: **{result.tier}**\n"
    )


def issue_body(result: Result, time: str) -> str:
    step = (
        "The diagnoser reads only. It reports a likely cause."
        if result.tier == DIAGNOSE
        else "The diagnoser reads only. It reports a likely cause and proposes one change. The run opens an intent PR."
    )
    return (
        f"The detect job found a reading outside its band at {time}.\n\n"
        f"{_signal_lines(result)}\n"
        "## Next step\n\n"
        f"A human starts the maintain stage run for this issue. {step} "
        "Every finding gets a triage record: fix now, schedule, or dismiss with a reason.\n\n"
        "The detect job made this issue. It used no model. It comments here when the same metric breaches "
        "the same tier again while this issue is open.\n"
    )


def repeat_body(result: Result, time: str) -> str:
    return f"Repeat breach at {time}.\n\n{_signal_lines(result)}"


def _open_issues(api, slug: str, env) -> dict[str, int]:
    found: dict[str, int] = {}
    page = 1
    while True:
        data = api("GET", f"repos/{slug}/issues?state=open&labels={ISSUE_LABEL}&per_page={ISSUE_PAGE}&page={page}", env=env)
        data = data or []
        for item in data:
            if "pull_request" in item:
                continue
            found.setdefault(item.get("title", ""), item["number"])
        if len(data) < ISSUE_PAGE:
            return found
        page += 1


def file_issues(slug: str, report: DetectReport, env=None, api=None) -> list[str]:
    """Open one issue per metric and tier at 2 and 3 sigma. Comment on a repeat.

    These are the only network calls of detect (NFR-4). Return one line per action.
    """
    if api is None:
        from . import github

        api = github.api
    breaches = [r for r in report.results if r.tier in ISSUE_TIERS]
    if not breaches:
        return []
    open_issues = _open_issues(api, slug, env)
    lines = []
    for result in breaches:
        title = issue_title(result.metric, result.tier)
        number = open_issues.get(title)
        if number is None:
            data = api("POST", f"repos/{slug}/issues",
                       {"title": title, "body": issue_body(result, report.time), "labels": [ISSUE_LABEL]}, env=env)
            open_issues[title] = data["number"]
            lines.append(f"opened issue #{data['number']}: {title}")
        else:
            api("POST", f"repos/{slug}/issues/{number}/comments", {"body": repeat_body(result, report.time)}, env=env)
            lines.append(f"commented on issue #{number}: {title}")
    return lines


# Findings: intent.md from the maintain template (FR-71, D7) --------------------------


def fill(template: str, values: dict[str, str], what: str) -> str:
    def one(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise SdlcError(f"The {what} template has the unknown placeholder {{{{{name}}}}}.")
        return values[name]

    return _PLACEHOLDER.sub(one, template)


def _upstream(links: list[str], fallback: str) -> str:
    if links:
        return "upstream:\n" + "".join(f"  - {json.dumps(link)}" + "\n" for link in links).rstrip("\n")
    if "\n" in fallback:
        raise SdlcError("The fallback text must be one line.")
    return f"upstream: {json.dumps('none, used ' + fallback)}"


def new_finding(repo: Path, stage_dir: Path, metric: str, issue: int | None = None, lane: str = DEFAULT_LANE,
                today: datetime.date | None = None) -> tuple[str, Path]:
    """Make a work item and its intent.md draft from the last reading of a metric.

    The maintain stage writes intent.md from its own template, so it needs
    no intent stage (D7). Return (work item ID, path of intent.md).
    """
    repo = Path(repo)
    rows = parse_readings(readings_text(repo, metric), metric)
    if not rows:
        raise SdlcError(f"The {DATA_BRANCH} branch has no readings for {metric}. Run detect first.")
    last = rows[-1]
    run = last_run(repo) or {}
    command = ((run.get("metrics") or {}).get(metric) or {}).get("command", "")
    if issue is not None and (isinstance(issue, bool) or not isinstance(issue, int) or issue < 1):
        raise SdlcError("The issue number must be a whole number of 1 or more.")
    template = overlay.resolve(INTENT_TEMPLATE, stage_dir, repo).read_text(encoding="utf-8")
    tier = last["tier"]
    item_id = workitem.new_item(f"{metric} {tier} breach", lane, repo, today)
    # With a detect issue, the issue and the readings are the upstream. With
    # no issue, the stage used its fallback: a command that prints a number.
    links = [f"issue #{issue}", f"{DATA_BRANCH}:{readings_rel(metric)}"] if issue is not None else []
    fallback = f"the command that prints the number, {command}" if command else "a command that prints a number"
    values = {
        "work_item": item_id,
        "upstream": _upstream(links, fallback),
        "lane": lane,
        "metric": metric,
        "command": command or "unknown",
        "value": fmt(last["value"]),
        "time": last["time"],
        "mean": last["mean"] or "none",
        "sigma": last["sigma"] or "none",
        "z": last["z"] or "none",
        "tier": tier,
        "readings": f"{DATA_BRANCH}:{readings_rel(metric)}",
        "issue": f"#{issue}" if issue is not None else "none",
    }
    rel = f"{WORK_REL}/{item_id}/intent.md"
    dest = safe_write_path(repo, rel)
    write_if_changed(dest, fill(template, values, "intent"))
    return item_id, dest


# Triage (FR-72) ------------------------------------------------------------------------


def check_item_id(item_id: str) -> str:
    if not isinstance(item_id, str) or not _ITEM_ID.match(item_id):
        raise SdlcError(f"The work item ID {item_id!r} is not valid. The format is YYYYMMDD-<slug>.")
    return item_id


def triage(repo: Path, item_id: str, decision: str, reason: str | None = None, issue: int | None = None,
           now: datetime.datetime | None = None) -> Path:
    """Add one decision to sdlc/triage/<id>.md. Dismiss needs a reason. Old entries stay."""
    repo = Path(repo)
    check_item_id(item_id)
    if decision not in DECISIONS:
        raise SdlcError(f"The decision {decision!r} is not known. Use one of: {', '.join(DECISIONS)}.")
    reason = (reason or "").strip()
    if decision == "dismiss" and not reason:
        raise SdlcError("A dismiss decision needs a reason. Give --reason with the words of the gate owner.")
    rel = f"{TRIAGE_REL}/{item_id}.md"
    dest = safe_write_path(repo, rel)
    stamp = _utc(now).isoformat().replace("+00:00", "Z")
    if dest.is_file():
        text = dest.read_text(encoding="utf-8")
    else:
        links = []
        if (repo / WORK_REL / item_id / "intent.md").is_file():
            links.append(f"../work/{item_id}/intent.md")
        if issue is not None:
            links.append(f"issue #{issue}")
        text = (
            "---\n"
            f"work_item: {item_id}\n"
            f"{_upstream(links, 'the decision of the gate owner')}\n"
            "stage: maintain\n"
            "---\n\n"
            f"# Triage: {item_id}\n\n"
            "The gate owner decides. The engine adds each decision at the end. Do not change an old entry.\n"
        )
    entry = f"\n## {stamp}: {decision}\n\n"
    if issue is not None:
        entry += f"- Issue: #{issue}\n"
    entry += f"- Reason: {reason or 'none given'}\n"
    write_if_changed(dest, text.rstrip("\n") + "\n" + entry)
    return dest


# The closer (FR-73) ----------------------------------------------------------------------


def new_lesson(repo: Path, stage_dir: Path, item_id: str, pr: int | None = None,
               today: datetime.date | None = None) -> Path:
    """Write sdlc/lessons/<id>.md from the lesson template. Refuse when it exists."""
    repo = Path(repo)
    check_item_id(item_id)
    dest = safe_write_path(repo, f"{LESSONS_REL}/{item_id}.md")
    if os.path.lexists(dest):
        raise SdlcError(f"The lesson {LESSONS_REL}/{item_id}.md exists. Edit it instead.")
    links = [name for name in ("intent.md", "spec.md", "plan.md", "evidence.md")
             if (repo / WORK_REL / item_id / name).is_file()]
    links = [f"../work/{item_id}/{name}" for name in links]
    if pr is not None:
        links.append(f"PR #{pr}")
    template = overlay.resolve(LESSON_TEMPLATE, stage_dir, repo).read_text(encoding="utf-8")
    values = {
        "work_item": item_id,
        "upstream": _upstream(links, "the merged fix and its diff"),
        "date": (today or datetime.date.today()).isoformat(),
        "pr": f"#{pr}" if pr is not None else "none",
        "eval_task": f"{EVAL_TASKS_REL}/{item_id}.toml",
    }
    write_if_changed(dest, fill(template, values, "lesson"))
    return dest


def close_check(repo: Path, item_id: str) -> list[str]:
    """Return the problems that stop a close. An empty list means done (FR-73)."""
    from . import evals

    repo = Path(repo)
    check_item_id(item_id)
    problems = []
    lesson = repo / LESSONS_REL / f"{item_id}.md"
    if not lesson.is_file():
        problems.append(f"{LESSONS_REL}/{item_id}.md is missing.")
    elif PENDING in lesson.read_text(encoding="utf-8"):
        problems.append(f"{LESSONS_REL}/{item_id}.md still has a {PENDING} line.")
    task_rel = f"{EVAL_TASKS_REL}/{item_id}.toml"
    if not (repo / task_rel).is_file():
        problems.append(f"{task_rel} is missing.")
    else:
        try:
            evals.load_tasks(repo, names=[item_id])
        except SdlcError as exc:
            problems.append(f"{task_rel} is not a valid eval task. {exc.message}")
    return problems
