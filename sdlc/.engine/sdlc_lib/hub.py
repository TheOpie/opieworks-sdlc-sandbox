"""The hub views: status and next (FR-80 to FR-84). Owner: the hub author.

The hub reads only. It reads the repo with git plumbing and GitHub with the
login of the person who runs it. It never writes a file, pushes, comments, or
calls GitHub as the bot.

Sources (spec section 8, IDs):
- Artifacts on the default branch: sdlc/work/<id>/{item.toml, intent.md,
  spec.md, plan.md, evidence.md, review.md}, sdlc/triage/, sdlc/lessons/.
- Pull requests: the work item and the stage come from the head branch
  sdlc/<id>/<stage> and the label sdlc:<stage>. Never from the PR author.
- Open PRs: evidence.md, review.md, and spec.md at the PR head, and the reviews.
- Production runs of the deploy workflow, by the work item in the run name.
- Open maintain issues and the last detect run (FR-84).

No stage needs this module (FR-83).
"""

from __future__ import annotations

import base64
import datetime
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import common, deploy, design, github, intent, maintain
from .common import WORK_REL, SdlcError, parse_frontmatter

STAGES = ("intent", "design", "build", "testing", "deploy", "maintain")
SKILL_PREFIX = "opieworks-sdlc-"
STALE_DAYS = 50  # FR-84
OWNER_KEY = "codeowners.owner"
SETUP_PREFIX = "sdlc/setup/"
MAX_PAGES = 20

# The stage part of a branch name sdlc/<id>/<part>, as the stage skills name them.
BRANCH_STAGES = {
    "intent": "intent", "triage": "intent", "design": "design", "plan": "build", "build": "build",
    "testing": "testing", "deploy": "deploy", "maintain": "maintain",
}
CODE_PARTS = ("build", "testing", "deploy")
_ITEM = r"[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*"
_ITEM_RE = re.compile(rf"^{_ITEM}$")
_BRANCH_RE = re.compile(rf"^sdlc/({_ITEM})/([a-z]+)$")
_RUN_ITEM = re.compile(rf"\(({_ITEM})\)\s*$")
_TRIAGE_ENTRY = re.compile(r"^## (\S+): (fix-now|schedule|dismiss)\s*$")
_TRIAGE_REASON = re.compile(r"^- Reason: (.*)$")
_PASS_VERDICTS = ("PASS", "PASS AFTER RETRY")


# Stage folders (FR-80, FR-83) ------------------------------------------------------


def find_stages(hub_dir: Path | None, bundles: list[Path] | None = None) -> dict[str, Path]:
    """Return {stage id: folder} for the stage skills next to the hub and the given bundles.

    A given bundle wins over a sibling folder. A folder counts only when its
    stage.toml loads and names one of the six stages.
    """
    found: dict[str, Path] = {}
    candidates = [Path(b) for b in bundles or []]
    if hub_dir is not None:
        parent = Path(hub_dir).absolute().parent
        candidates += [parent / f"{SKILL_PREFIX}{stage}" for stage in STAGES]
    for folder in candidates:
        if not (folder / "stage.toml").is_file():
            continue
        try:
            stage_id = common.load_stage(folder)["id"]
        except SdlcError:
            continue
        if stage_id in STAGES and stage_id not in found:
            found[stage_id] = folder
    return found


# Reads from git ----------------------------------------------------------------------


def _show(repo: Path, ref: str, rel: str) -> str | None:
    result = github._run([github.GIT, "show", f"{ref}:{rel}"], cwd=repo)
    return result.stdout if result.returncode == 0 else None


def _sha(repo: Path, ref: str) -> str | None:
    result = github._run([github.GIT, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], cwd=repo)
    return result.stdout.strip() or None if result.returncode == 0 else None


def default_ref(repo: Path, branch: str) -> str:
    """Return origin/<branch> when it exists, else <branch>."""
    if _sha(repo, f"refs/remotes/origin/{branch}"):
        return f"origin/{branch}"
    if _sha(repo, f"refs/heads/{branch}"):
        return branch
    raise SdlcError(f"The repo has no branch {branch} and no origin/{branch}. Run git fetch.")


def read_artifacts(repo: Path, ref: str) -> dict[str, dict[str, str]]:
    """Return {id: {file name: text}} for every work item folder on ref."""
    listing = github.git(repo, "ls-tree", "-r", "-z", "--name-only", ref, "--", f"{WORK_REL}/")
    items: dict[str, dict[str, str]] = {}
    for path in (p for p in listing.split("\0") if p):
        parts = path.split("/")
        if len(parts) != 4 or not _ITEM_RE.match(parts[2]):
            continue
        if parts[3] == "item.toml" or parts[3].endswith(".md"):
            items.setdefault(parts[2], {})[parts[3]] = _show(repo, ref, path) or ""
    return items


def _names(repo: Path, ref: str, folder: str, suffix: str) -> dict[str, str]:
    listing = github.git(repo, "ls-tree", "-z", "--name-only", ref, "--", f"{folder}/")
    found = {}
    for path in (p for p in listing.split("\0") if p):
        name = path.rsplit("/", 1)[-1]
        if name.endswith(suffix) and _ITEM_RE.match(name[: -len(suffix)]):
            found[name[: -len(suffix)]] = path
    return found


def installed_stages(agents_text: str | None) -> list[str]:
    """Return the stage IDs that have a section in the AGENTS.md text, in file order."""
    found: list[str] = []
    for stage in re.findall(r"<!-- opieworks-sdlc:([a-z0-9][a-z0-9-]*):begin -->", agents_text or ""):
        if stage not in found and common.get_section(agents_text, stage, "md") is not None:
            found.append(stage)
    return found


def _meta(text: str | None) -> dict:
    if not text:
        return {}
    try:
        meta, _ = parse_frontmatter(text)
    except SdlcError:
        return {}
    return meta


def _body(text: str) -> str:
    try:
        return parse_frontmatter(text)[1]
    except SdlcError:
        return text


def last_triage(text: str) -> tuple[str, str] | None:
    """Return (decision, reason) of the last entry of a maintain triage record."""
    decision = None
    reason = ""
    for line in text.splitlines():
        entry = _TRIAGE_ENTRY.match(line)
        if entry:
            decision, reason = entry.group(2), ""
            continue
        found = _TRIAGE_REASON.match(line)
        if found and decision:
            reason = found.group(1).strip()
    return (decision, reason) if decision else None


# Reads from GitHub ---------------------------------------------------------------------


def _pages(api, path: str) -> list[dict]:
    found = []
    joiner = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        data = api("GET", f"{path}{joiner}per_page=100&page={page}") or []
        if not isinstance(data, list):
            raise SdlcError(f"GitHub did not give a list for {path}.")
        found += [item for item in data if isinstance(item, dict)]
        if len(data) < 100:
            break
    return found


def _content(api, slug: str, rel: str, ref: str) -> str | None:
    try:
        data = api("GET", f"repos/{slug}/contents/{rel}?ref={ref}")
    except github.NotFound:
        return None
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return None
    return base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace")


def _approved(api, slug: str, number: int) -> bool:
    """True when the latest review of some user approves and no latest review asks for changes.

    It reads review states only. The engine has no call that approves (FR-10).
    """
    latest: dict[str, str] = {}
    for review in _pages(api, f"repos/{slug}/pulls/{number}/reviews"):
        login = (review.get("user") or {}).get("login") or ""
        state = str(review.get("state") or "").lower()
        if state in ("approved", "changes_requested", "dismissed"):
            latest[login] = state
    states = set(latest.values())
    return "approved" in states and "changes_requested" not in states


def production_runs(api, slug: str) -> list[dict]:
    """Return the production runs, newest first, with the work item from the run name."""
    try:
        data = api("GET", f"repos/{slug}/actions/workflows/{deploy.PRODUCTION_WORKFLOW}/runs?per_page=100") or {}
    except github.NotFound:
        return []
    rows = []
    for run in data.get("workflow_runs") or []:
        title = run.get("display_title") or ""
        item = _RUN_ITEM.search(title)
        rows.append({
            "item": item.group(1) if item else None, "status": run.get("status"),
            "conclusion": run.get("conclusion"), "url": run.get("html_url"), "created": run.get("created_at"),
        })
    return rows


# The snapshot ----------------------------------------------------------------------------


@dataclass
class Snapshot:
    """Everything that status and next read. Built once, then read only."""

    slug: str
    default_branch: str
    ref: str
    ref_sha: str | None
    remote_sha: str | None
    owner: str | None
    installed: list[str]
    artifacts: dict[str, dict[str, str]]
    triage: dict[str, str]
    lessons: set[str]
    intent_triage: dict[str, dict]
    pulls: list[dict]
    branches: list[str]
    runs: list[dict]
    issues: list[dict]
    last_run: dict | None
    now: datetime.datetime
    api: object = None


def snapshot(repo: Path, api=None, ref: str | None = None, now: datetime.datetime | None = None) -> Snapshot:
    """Read the repo and GitHub once. Read only."""
    repo = Path(repo)
    api = api or (lambda method, path, body=None: github.api(method, path, body))
    slug = github.origin_slug(repo)
    branch = (api("GET", f"repos/{slug}") or {})["default_branch"]
    ref = ref or default_ref(repo, branch)
    try:
        remote_sha = ((api("GET", f"repos/{slug}/branches/{branch}") or {}).get("commit") or {}).get("sha")
    except github.NotFound:
        remote_sha = None
    config_text = _show(repo, ref, common.CONFIG_REL)
    try:
        config = tomllib.loads(config_text) if config_text else {}
    except tomllib.TOMLDecodeError:
        raise SdlcError(f"{common.CONFIG_REL} on {ref} is not valid TOML.") from None
    owner = (config.get("codeowners") or {}).get("owner")
    owner = owner.lstrip("@") if isinstance(owner, str) and owner.strip() and owner != "OWNER" else None
    triage_paths = _names(repo, ref, maintain.TRIAGE_REL, ".md")
    return Snapshot(
        slug=slug, default_branch=branch, ref=ref, ref_sha=_sha(repo, ref), remote_sha=remote_sha, owner=owner,
        installed=installed_stages(_show(repo, ref, "AGENTS.md")),
        artifacts=read_artifacts(repo, ref),
        triage={item: _show(repo, ref, path) or "" for item, path in triage_paths.items()},
        lessons=set(_names(repo, ref, maintain.LESSONS_REL, ".md")),
        intent_triage=intent.read_triage(repo, ref),
        pulls=_pages(api, f"repos/{slug}/pulls?state=all"),
        branches=[b.get("name") or "" for b in _pages(api, f"repos/{slug}/branches")],
        runs=production_runs(api, slug),
        issues=[i for i in _pages(api, f"repos/{slug}/issues?state=open&labels={maintain.ISSUE_LABEL}")
                if "pull_request" not in i],
        last_run=maintain.last_run(repo),
        now=now or datetime.datetime.now(datetime.timezone.utc),
        api=api,
    )


# The state of one work item --------------------------------------------------------------


@dataclass
class ItemState:
    item: str
    stage: str
    lane: str | None
    gate: str
    pr: str | None = None
    waiting_on: str | None = None   # who must act at the gate
    action: str | None = None        # what that human must do
    next_stage: str | None = None
    next_step: str | None = None     # the stage procedure
    command: str | None = None       # with {skill} for the stage folder

    def to_dict(self) -> dict:
        return asdict(self)


def _pull_item(pull: dict) -> tuple[str, str] | None:
    """Return (item, branch part) from the head branch. The label may name the stage too."""
    match = _BRANCH_RE.match(((pull.get("head") or {}).get("ref")) or "")
    if not match or match.group(2) not in BRANCH_STAGES:
        return None
    return match.group(1), match.group(2)


def _label_stage(pull: dict) -> str | None:
    for label in pull.get("labels") or []:
        name = (label or {}).get("name") or ""
        if name.startswith("sdlc:") and name[5:] in STAGES:
            return name[5:]
    return None


def _newest(pulls: list[dict]) -> dict | None:
    return max(pulls, key=lambda p: int(p.get("number") or 0)) if pulls else None


def item_ids(snap: Snapshot) -> list[str]:
    ids = set(snap.artifacts) | set(snap.triage) | snap.lessons
    ids |= {found[0] for found in map(_pull_item, snap.pulls) if found}
    ids |= {m.group(1) for m in map(_BRANCH_RE.match, snap.branches) if m and m.group(2) in BRANCH_STAGES}
    ids |= {run["item"] for run in snap.runs if run["item"]}
    return sorted(ids)


def _lane(files: dict[str, str]) -> str | None:
    if "item.toml" in files:
        try:
            lane = tomllib.loads(files["item.toml"]).get("lane")
        except tomllib.TOMLDecodeError:
            lane = None
        if isinstance(lane, str) and lane:
            return lane
    for name in ("intent.md", "plan.md"):
        lane = _meta(files.get(name)).get("lane")
        if isinstance(lane, str) and lane:
            return lane
    return None


def _open_flags(text: str | None) -> int | None:
    """Count the open flags of a spec.md text with the design parser. None when it does not parse."""
    if not text:
        return None
    try:
        return sum(1 for flag in design.parse_flags(_body(text)) if flag.open)
    except SdlcError:
        return None


def _owner(snap: Snapshot) -> str:
    return f"@{snap.owner}" if snap.owner else f"the gate owner (set {OWNER_KEY} in {common.CONFIG_REL})"


def item_state(item: str, snap: Snapshot) -> ItemState:
    """Return the stage, lane, and gate state of one work item, and the next step."""
    files = snap.artifacts.get(item, {})
    lane = _lane(files)
    owner = _owner(snap)
    by_part: dict[str, list[dict]] = {}
    for pull in snap.pulls:
        found = _pull_item(pull)
        if found and found[0] == item:
            by_part.setdefault(found[1], []).append(pull)
    state = ItemState(item=item, stage="intent", lane=lane, gate="")

    def waiting(gate: str, action: str, pull: dict | None = None) -> ItemState:
        state.gate, state.waiting_on, state.action = gate, owner, action
        state.pr = (pull or {}).get("html_url") or state.pr
        return state

    def go(stage: str, step: str, command: str | None = None) -> ItemState:
        state.next_stage, state.next_step, state.command = stage, step, command
        return state

    # Maintain: closed, open closer or dismiss PR, or a dismissal.
    if item in snap.lessons:
        state.stage, state.gate = "maintain", "done: the lesson is on the default branch"
        return state
    maintain_pr = _newest([p for p in by_part.get("maintain", []) if p.get("state") == "open"])
    if maintain_pr:
        state.stage = "maintain"
        return waiting(f"maintain PR #{maintain_pr['number']} open", "review and merge the PR", maintain_pr)
    record = last_triage(snap.triage[item]) if item in snap.triage else None
    if record and record[0] == "dismiss":
        state.stage, state.gate = "maintain", f"dismissed: {record[1] or 'no reason'}"
        return state

    # Deploy: production runs of this work item, newest first.
    runs = [r for r in snap.runs if r["item"] == item]
    code_pulls = [p for part in CODE_PARTS for p in by_part.get(part, [])]
    merged_code = _newest([p for p in code_pulls if p.get("merged_at")])
    if runs:
        run = runs[0]
        state.stage, state.pr = "deploy", run["url"]
        if run["status"] == "waiting":
            return waiting("production run waits for the environment approval",
                           "approve or reject the production deployment in GitHub Actions")
        if run["status"] != "completed":
            state.gate = f"production run {run['status']}"
            return state
        if run["conclusion"] == "success":
            state.gate = "production done"
            pr = f" --pr {merged_code['number']}" if merged_code else " --pr <n>"
            return go("maintain", "closer: write the lesson and the eval task of a bug fix",
                      f"python3 {{skill}}/core/sdlc.py lesson {item}{pr}")
        state.gate = f"production run {run['conclusion']}"
        return go("deploy", "rollback", "python3 {skill}/core/sdlc.py deploy history")

    # The code PR: build, testing, and the deploy review, on the PR head.
    if merged_code:
        state.stage, state.pr = "deploy", merged_code.get("html_url")
        state.gate = f"PR #{merged_code['number']} merged, no production run"
        return go("deploy", "run deploy: canary, then production",
                  f"python3 {{skill}}/core/sdlc.py deploy canary {item} --tag <tag>")
    open_code = _newest([p for p in code_pulls if p.get("state") == "open"])
    if open_code:
        number, head = open_code["number"], (open_code.get("head") or {}).get("sha") or open_code["head"]["ref"]
        state.pr = open_code.get("html_url")
        evidence = _meta(_content(snap.api, snap.slug, f"{WORK_REL}/{item}/evidence.md", head) or files.get("evidence.md"))
        review = _meta(_content(snap.api, snap.slug, f"{WORK_REL}/{item}/review.md", head) or files.get("review.md"))
        verdict = str(evidence.get("verdict") or "")
        if not evidence:
            state.stage, state.gate = "build", f"PR #{number} open, no evidence"
            return go("testing", "run", f"python3 {{skill}}/core/sdlc.py verify {item}")
        if verdict not in _PASS_VERDICTS:
            state.stage, state.gate = "testing", f"PR #{number} evidence {verdict or 'has no verdict'}"
            return go("build", "fix the code on the PR branch, then run the testing stage again",
                      f"python3 {{skill}}/core/sdlc.py role implementer --harness <h> --input {WORK_REL}/{item}/evidence.md")
        if not review:
            state.stage, state.gate = "testing", f"PR #{number} evidence {verdict}"
            return go("deploy", "run review",
                      f"python3 {{skill}}/core/sdlc.py role reviewer --harness <h> --input REVIEW.md --input <tmp>/diff.patch --input <tmp>/pr.md")
        state.stage = "deploy"
        rv = str(review.get("verdict") or "no verdict")
        if _approved(snap.api, snap.slug, number):
            return waiting(f"PR #{number} review {rv}, approved", "merge the PR", open_code)
        if rv == "FINDINGS":
            waiting(f"PR #{number} review FINDINGS, not approved", "read the findings, then approve or ask for changes", open_code)
            return go("deploy", "run babysit", f"python3 {{skill}}/core/sdlc.py deploy babysit {number} --harness <h>")
        return waiting(f"PR #{number} review {rv}, not approved", "review, approve, and merge the PR", open_code)

    # Build: the plan.
    plan_pr = _newest(by_part.get("plan", []))
    plan = _meta(files.get("plan.md"))
    if plan_pr and plan_pr.get("state") == "open":
        state.stage = "build"
        return waiting(f"plan PR #{plan_pr['number']} open", "review the plan, then merge the plan PR to accept it", plan_pr)
    if plan:
        state.stage = "build"
        if (lane or plan.get("lane")) == "fast" and plan.get("breach") not in (None, "", "none"):
            state.gate = f"fast-lane breach: {plan.get('breach')}"
            waiting(state.gate, f"set the lane to full: python3 {{skill}}/core/sdlc.py intent lane {item} --lane full")
            return go("design", "run", f"python3 {{skill}}/core/sdlc.py role spec-writer --harness <h> --input {WORK_REL}/{item}/intent.md")
        state.gate = "plan accepted (on the default branch)"
        return go("build", "run, part C: the code",
                  f"python3 {{skill}}/core/sdlc.py build-check {item} --phase code --base {snap.default_branch}")

    # Design: the spec.
    design_pr = _newest(by_part.get("design", []))
    if design_pr and design_pr.get("state") == "open":
        state.stage = "design"
        head = (design_pr.get("head") or {}).get("sha") or design_pr["head"]["ref"]
        flags = _open_flags(_content(snap.api, snap.slug, f"{WORK_REL}/{item}/spec.md", head))
        open_text = "flags not read" if flags is None else f"{flags} open flag(s)"
        action = "resolve each open flag, then merge the design PR" if flags else "review and merge the design PR"
        return waiting(f"design PR #{design_pr['number']} open, {open_text}", action, design_pr)
    if "spec.md" in files:
        state.stage, state.gate = "design", "spec accepted (on the default branch)"
        return go("build", "run, part A: the plan", f"python3 {{skill}}/core/sdlc.py plan-init {item}")

    # Intent: the intent PR and its gate (FR-22, SCN-10).
    pulls = [p for p in by_part.get("intent", []) if _label_stage(p) in (None, "intent")]
    # The branch sdlc/<id>/intent names an intent PR. Add the label when a human removed it.
    labeled = [dict(p, labels=list(p.get("labels") or []) + [{"name": intent.LABEL}]) for p in pulls]
    states = intent.intent_states(labeled, snap.intent_triage)
    gate = states[0] if states else None
    if gate and gate.state == intent.OPEN:
        return waiting(f"intent PR #{gate.pr_number} open", "accept by merge, or reject by close with a reason",
                       {"html_url": gate.pr_url})
    if gate and gate.state == intent.REJECTED_NO_REASON:
        state.pr = gate.pr_url
        waiting(intent.REJECTED_NO_REASON, "give the reason for the close")
        return go("intent", "record the reason", f'python3 {{skill}}/core/sdlc.py intent record-reason {item} --reason "<reason>"')
    if gate and gate.state == intent.REJECTED:
        state.gate, state.pr = f"rejected: {gate.reason}", gate.pr_url
        return state
    if (gate and gate.state == intent.ACCEPTED) or "intent.md" in files:
        state.gate = "intent accepted"
        if gate:
            state.pr = gate.pr_url
        if lane == "fast":  # FR-11: the fast lane skips design.
            return go("build", "run, part A: the plan (fast lane, design skipped)",
                      f"python3 {{skill}}/core/sdlc.py plan-init {item}")
        if lane == "full":  # FR-33: next names design after an intent merges.
            return go("design", "run", f"python3 {{skill}}/core/sdlc.py role spec-writer --harness <h> --input {WORK_REL}/{item}/intent.md")
        waiting("intent accepted, no lane", f"set the lane: python3 {{skill}}/core/sdlc.py intent lane {item} --lane <fast|full>")
        return state
    pushed = [b for b in snap.branches if (m := _BRANCH_RE.match(b)) and m.group(1) == item]
    state.gate = f"branch {pushed[0]} pushed, no PR" if pushed else "no intent yet"
    return go("intent", "run", None)


def find_item(item: str, snap: Snapshot) -> ItemState:
    if not _ITEM_RE.match(item or ""):
        raise SdlcError(f"The work item ID {item!r} is not valid. The format is YYYYMMDD-<slug>.")
    if item not in item_ids(snap):
        raise SdlcError(f"The work item {item} has no folder on {snap.ref}, no sdlc branch, no PR, and no production run.")
    return item_state(item, snap)


# The status view (FR-81, FR-84) ---------------------------------------------------------


def _parse_time(text: str) -> datetime.datetime:
    value = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)


def warnings(snap: Snapshot) -> list[str]:
    lines = []
    if snap.remote_sha and snap.ref_sha and snap.remote_sha != snap.ref_sha:
        lines.append(f"{snap.ref} is at {snap.ref_sha[:7]}, but GitHub has {snap.default_branch} at "
                     f"{snap.remote_sha[:7]}. Run git fetch, then status again.")
    if snap.last_run:
        age = (snap.now - _parse_time(snap.last_run["time"])).days
        if age > STALE_DAYS:
            lines.append(f"The last detect run was {age} days ago ({snap.last_run['time']}). "
                         f"That is more than {STALE_DAYS} days. Start the detect workflow (FR-84).")
    elif "maintain" in snap.installed:
        lines.append(f"The maintain stage is installed, but detect never ran (no {maintain.LAST_RUN_REL} "
                     f"on {maintain.DATA_BRANCH}). Start the detect workflow (FR-84).")
    return lines


def status(snap: Snapshot) -> dict:
    items = [item_state(item, snap) for item in item_ids(snap)]
    setup = [{"number": p["number"], "branch": p["head"]["ref"], "url": p.get("html_url")}
             for p in snap.pulls if p.get("state") == "open" and ((p.get("head") or {}).get("ref") or "").startswith(SETUP_PREFIX)]
    return {
        "repo": snap.slug,
        "default_branch": snap.default_branch,
        "ref": snap.ref,
        "ref_sha": snap.ref_sha,
        "gate_owner": snap.owner,
        "installed": snap.installed,
        "setup_prs": sorted(setup, key=lambda p: p["number"]),
        "items": [s.to_dict() for s in items],
        "maintain_issues": [{"number": i.get("number"), "title": i.get("title"), "url": i.get("html_url")}
                            for i in snap.issues],
        "last_detect": snap.last_run["time"] if snap.last_run else None,
        "warnings": warnings(snap),
    }


def render_status(data: dict) -> list[str]:
    lines = [f"repo: {data['repo']}  ref: {data['ref']} ({(data['ref_sha'] or '-')[:7]})  "
             f"gate owner: {('@' + data['gate_owner']) if data['gate_owner'] else 'not set'}",
             f"installed stages: {', '.join(data['installed']) or 'none'}"]
    for pr in data["setup_prs"]:
        lines.append(f"setup PR #{pr['number']} open ({pr['branch']}): waits for the gate owner. {pr['url'] or ''}".rstrip())
    rows = [("ITEM", "STAGE", "LANE", "GATE", "NEXT")]
    for s in data["items"]:
        rows.append((s["item"], s["stage"], s["lane"] or "-", s["gate"], "gate" if s["waiting_on"] else (s["next_stage"] or "-")))
    if len(rows) == 1:
        lines.append("work items: none")
    else:
        widths = [max(len(r[i]) for r in rows) for i in range(4)]
        lines += ["  ".join(r[i].ljust(widths[i]) for i in range(4)) + "  " + r[4] for r in rows]
    for issue in data["maintain_issues"]:
        lines.append(f"maintain issue #{issue['number']} open: {issue['title']}")
    lines.append(f"last detect run: {data['last_detect'] or 'none'}")
    lines += [f"warning: {w}" for w in data["warnings"]]
    return lines


# The next view (FR-82) -------------------------------------------------------------------


def next_step(state: ItemState, stages: dict[str, Path]) -> dict:
    """Return the next step of a work item with the stage folder in the command."""
    data = state.to_dict()
    for key, stage in (("command", state.next_stage), ("action", "intent")):
        text = data.get(key)
        if text and "{skill}" in text:
            folder = stages.get(stage)
            data[key] = text.replace("{skill}", str(folder) if folder else f"<{SKILL_PREFIX}{stage} folder>")
    data["skill"] = f"{SKILL_PREFIX}{state.next_stage}" if state.next_stage else None
    return data


def render_next(data: dict) -> list[str]:
    lines = [f"item: {data['item']}", f"stage: {data['stage']}  lane: {data['lane'] or '-'}", f"gate: {data['gate']}"]
    if data["pr"]:
        lines.append(f"link: {data['pr']}")
    if data["waiting_on"]:
        lines.append(f"waiting on: {data['waiting_on']}: {data['action']}")
    if data["next_stage"]:
        lines.append(f"next stage: {data['next_stage']} (skill {data['skill']}, {data['next_step']})")
        lines.append(f"command: {data['command'] or 'follow the run procedure of ' + data['skill']}")
    elif not data["waiting_on"]:
        lines.append("next stage: none. The work item is done or closed.")
    return lines
