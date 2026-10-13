"""The intent stage (Plan): intent.md, the lane rule, the intent PR, and the
intent gate state (FR-11, FR-20, FR-21, FR-22, SCN-10). Owner: the intent stage.

The engine does the mechanical work. It makes the work item, fills the
template, checks intent.md, opens the PR as the bot with the label
sdlc:intent, and reads the gate state from GitHub and the repo. The
analyst role and the human do the judgment work.

A merged intent PR means that the intent is accepted. A closed intent PR
that is not merged means that the intent is rejected. The reason for a
rejection lives in sdlc/triage/. Until a reason is there, the state is
"rejected, no reason". The engine never merges, closes, accepts, or
rejects an intent (FR-10). It records a reason only for a PR that a human
already closed.
"""

from __future__ import annotations

import datetime
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import github, overlay, workitem
from .common import (
    WORK_REL,
    SdlcError,
    dump_toml,
    load_toml,
    parse_frontmatter,
    safe_join,
    safe_write_path,
    write_if_changed,
)

STAGE = "intent"
LABEL = "sdlc:intent"
CONFIG_TABLE = "intent"
SECTIONS_KEY = "sections"
DEFAULT_SECTIONS = (
    "Problem",
    "Outcome",
    "Affected users and systems",
    "Constraints",
    "Open questions",
    "Lane",
    "Done when",
)
INTENT_NAME = "intent.md"
TEMPLATE = "templates/intent-{kind}.md"
KINDS = ("bug", "feature")
# CLI source value -> the text in the source frontmatter key.
SOURCES = {"text": "free text", "thread": "thread", "issue": "issue"}
CHANGE_KEYS = ("api_change", "token_change", "visual_change")
CHANGE_VALUES = ("yes", "no", "unknown")
FILL_PREFIX = "FILL:"
NO_UPSTREAM_PREFIX = "none, used "
TRIAGE_REL = "sdlc/triage"
TRIAGE_PREFIX = "intent-"

# Gate states.
OPEN = "open"
ACCEPTED = "accepted"
REJECTED = "rejected"
REJECTED_NO_REASON = "rejected, no reason"

_ITEM_ID = r"[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*"
_ITEM_RE = re.compile(rf"^{_ITEM_ID}$")
_BRANCH_RE = re.compile(rf"^sdlc/({_ITEM_ID})/{STAGE}$")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*#*[ \t]*$")


def check_item_id(item_id: str) -> str:
    if not isinstance(item_id, str) or not _ITEM_RE.match(item_id):
        raise SdlcError(f"The work item ID {item_id!r} is not valid. The format is YYYYMMDD-<slug>.")
    return item_id


def branch_name(item_id: str) -> str:
    """Return the intent branch of a work item: sdlc/<id>/intent."""
    return f"sdlc/{check_item_id(item_id)}/{STAGE}"


def item_from_branch(branch: str) -> str | None:
    """Return the work item ID of an intent branch, else None."""
    match = _BRANCH_RE.match(branch or "")
    return match.group(1) if match else None


# The lane rule (FR-11) -------------------------------------------------------------


def _check_changes(changes: dict) -> dict:
    out = {}
    for key in CHANGE_KEYS:
        value = changes.get(key, "unknown")
        if value not in CHANGE_VALUES:
            raise SdlcError(f"The value of {key} must be one of: {', '.join(CHANGE_VALUES)}.")
        out[key] = value
    return out


def propose_lane(kind: str, changes: dict) -> tuple[str, str]:
    """Return (lane, reason). Fast lane = a bug with no API, token, or visual change.

    An unknown answer counts as a change, so it gives the full lane.
    """
    if kind not in KINDS:
        raise SdlcError(f"The kind {kind!r} is not known. Use one of: {', '.join(KINDS)}.")
    changes = _check_changes(changes)
    if kind != "bug":
        return "full", "A feature always uses the full lane."
    not_no = [key.replace("_", " ") for key in CHANGE_KEYS if changes[key] != "no"]
    if not_no:
        return "full", "The answer for " + ", ".join(not_no) + " is yes or unknown."
    return "fast", "The bug has no API change, no token change, and no visual change."


def lane_problem(kind: str, lane: str, changes: dict) -> str | None:
    """Return why the lane is not allowed, else None. The full lane is always allowed."""
    if lane not in workitem.LANES:
        return f"The lane {lane!r} is not known. Use one of: {', '.join(workitem.LANES)}."
    if lane == "fast" and propose_lane(kind, changes)[0] != "fast":
        return "The fast lane is only for a bug with no API change, no token change, and no visual change."
    return None


# intent.md ------------------------------------------------------------------------


def render(template: str, values: dict[str, str]) -> str:
    """Fill {{name}} placeholders. An unknown name raises SdlcError."""

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise SdlcError(f"The intent template has the unknown placeholder {{{{{name}}}}}.")
        return values[name]

    return _PLACEHOLDER.sub(fill, template)


def upstream_lines(source: str, links: list[str] | None) -> str:
    """Return the upstream frontmatter (FR-3, FR-6)."""
    found = [link.strip() for link in (links or []) if link.strip()]
    if found:
        return "upstream:\n" + "".join(f"  - {json.dumps(link)}\n" for link in found)
    return f"upstream: {json.dumps(NO_UPSTREAM_PREFIX + source)}\n"


def _intent_path(repo: Path, item_id: str) -> Path:
    return safe_join(Path(repo), f"{WORK_REL}/{check_item_id(item_id)}/{INTENT_NAME}")


def new_intent(title: str, kind: str, source: str, repo: Path, stage_dir: Path,
               changes: dict | None = None, links: list[str] | None = None,
               today: datetime.date | None = None) -> tuple[str, str, str]:
    """Make the work item and write sdlc/work/<id>/intent.md from the template.

    Return (item ID, proposed lane, lane reason). The template comes from the
    overlay first, then from the stage folder (FR-4, FR-21).
    """
    if kind not in KINDS:
        raise SdlcError(f"The kind {kind!r} is not known. Use one of: {', '.join(KINDS)}.")
    if source not in SOURCES:
        raise SdlcError(f"The source {source!r} is not known. Use one of: {', '.join(SOURCES)}.")
    source_text = SOURCES[source]
    if source == "issue" and not [link for link in (links or []) if link.strip()]:
        raise SdlcError("An issue source needs the issue URL. Give it with --upstream.")
    changes = _check_changes(changes or {})
    lane, reason = propose_lane(kind, changes)
    template = overlay.resolve(TEMPLATE.format(kind=kind), stage_dir, repo).read_text(encoding="utf-8")
    title = title.strip()
    # Fill the template before the work item exists, so a bad template makes no folder.
    values = {
        "work_item": "",
        "upstream": upstream_lines(source_text, links).rstrip("\n"),
        "kind": kind,
        "source": source_text,
        "lane": lane,
        "lane_reason": reason,
        "api_change": changes["api_change"],
        "token_change": changes["token_change"],
        "visual_change": changes["visual_change"],
        "title": title,
    }
    render(template, values)
    item_id = workitem.new_item(title, lane, repo, today)
    values["work_item"] = item_id
    dest = safe_write_path(Path(repo), f"{WORK_REL}/{item_id}/{INTENT_NAME}")
    write_if_changed(dest, render(template, values))
    return item_id, lane, reason


def sections(config: dict) -> list[str]:
    """Return the required intent.md sections from the [intent] config table."""
    table = config.get(CONFIG_TABLE)
    value = table.get(SECTIONS_KEY) if isinstance(table, dict) else None
    if value is None:
        return list(DEFAULT_SECTIONS)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise SdlcError(f"The config key {CONFIG_TABLE}.{SECTIONS_KEY} must be a list of section titles.")
    return [v.strip() for v in value]


def _headings(body: str) -> list[str]:
    found = []
    fence = None
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            mark = stripped[:3]
            fence = None if fence == mark else (fence or mark)
            continue
        if fence:
            continue
        match = _HEADING.match(line)
        if match:
            found.append(match.group(1).strip())
    return found


def _section_text(body: str, title: str) -> str:
    """Return the text under the "## <title>" heading, up to the next "## " heading."""
    out = []
    inside = False
    for line in body.splitlines():
        match = _HEADING.match(line)
        if match:
            if inside:
                break
            inside = match.group(1).strip().lower() == title.lower()
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def check_intent(item_id: str, repo: Path, config: dict) -> list[str]:
    """Return the problems of sdlc/work/<id>/intent.md. An empty list means that it is complete."""
    path = _intent_path(repo, item_id)
    if not path.is_file():
        raise SdlcError(f"The work item {item_id} has no {INTENT_NAME}. Run the intent new command first.")
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except SdlcError as exc:
        return [f"The frontmatter is not valid. {exc.message}"]
    problems = []
    if meta.get("work_item") != item_id:
        problems.append(f"The frontmatter key work_item must be {item_id}.")
    if meta.get("stage") != STAGE:
        problems.append(f"The frontmatter key stage must be {STAGE}.")
    upstream = meta.get("upstream")
    if not upstream:
        problems.append("The frontmatter key upstream is missing. Give links, or \"none, used <fallback>\".")
    kind = meta.get("kind")
    if kind not in KINDS:
        problems.append(f"The frontmatter key kind must be one of: {', '.join(KINDS)}.")
    changes = {}
    for key in CHANGE_KEYS:
        value = meta.get(key)
        if value not in CHANGE_VALUES:
            problems.append(f"The frontmatter key {key} must be one of: {', '.join(CHANGE_VALUES)}.")
        else:
            changes[key] = value
    lane = meta.get("lane")
    if kind in KINDS and len(changes) == len(CHANGE_KEYS):
        problem = lane_problem(kind, lane, changes)
        if problem:
            problems.append(problem)
    elif lane not in workitem.LANES:
        problems.append(f"The frontmatter key lane must be one of: {', '.join(workitem.LANES)}.")
    item_toml = path.parent / workitem.ITEM_FILE
    if item_toml.is_file():
        item_lane = load_toml(item_toml).get("lane")
        if item_lane != lane:
            problems.append(f"The lane in {workitem.ITEM_FILE} is {item_lane!r}, but intent.md has {lane!r}. Use the intent lane command.")
    headings = [h.lower() for h in _headings(body)]
    for title in sections(config):
        if title.lower() not in headings:
            problems.append(f"The section \"## {title}\" is missing.")
        elif not _section_text(body, title):
            problems.append(f"The section \"## {title}\" is empty. Write \"none\" when it has no content.")
    for line in body.splitlines():
        if FILL_PREFIX in line:
            problems.append(f"A {FILL_PREFIX} line is still in the body: \"{line.strip()[:60]}\".")
    return problems


def _set_frontmatter_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SdlcError("intent.md has no frontmatter.")
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            break
        if line.partition(":")[0].strip() == key and not line[0].isspace():
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines[index] = f"{key}: {value}{newline}"
            return "".join(lines)
    raise SdlcError(f"The frontmatter of intent.md has no {key} key.")


def set_lane(item_id: str, repo: Path, lane: str) -> bool:
    """Set the lane in intent.md and item.toml. Refuse a fast lane that breaks the rule (FR-11).

    The analyst proposes the lane. The human sets it at the Plan gate.
    Return True when a file changed.
    """
    path = _intent_path(repo, item_id)
    if not path.is_file():
        raise SdlcError(f"The work item {item_id} has no {INTENT_NAME}.")
    text = path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    changes = {key: meta.get(key) for key in CHANGE_KEYS}
    kind = meta.get("kind")
    if kind not in KINDS:
        raise SdlcError(f"The frontmatter key kind must be one of: {', '.join(KINDS)}.")
    problem = lane_problem(kind, lane, _check_changes({k: v for k, v in changes.items() if v is not None}))
    if problem:
        raise SdlcError(problem)
    changed = write_if_changed(safe_write_path(Path(repo), f"{WORK_REL}/{item_id}/{INTENT_NAME}"),
                               _set_frontmatter_value(text, "lane", lane))
    item_rel = f"{WORK_REL}/{item_id}/{workitem.ITEM_FILE}"
    item_path = safe_write_path(Path(repo), item_rel)
    if item_path.is_file():
        data = load_toml(item_path)
        data["lane"] = lane
        changed = write_if_changed(item_path, dump_toml(data)) or changed
    return changed


def read_title(item_id: str, repo: Path) -> str:
    item_path = safe_join(Path(repo), f"{WORK_REL}/{check_item_id(item_id)}/{workitem.ITEM_FILE}")
    if item_path.is_file():
        title = load_toml(item_path).get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return item_id


# Sources (FR-20) ----------------------------------------------------------------


def issue_text(slug: str, number: int, env: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (issue URL, the issue and its comments as markdown text).

    It reads with the login of the person who runs the command. It writes nothing.
    """
    if number < 1:
        raise SdlcError("The issue number must be 1 or more.")
    issue = github.api("GET", f"repos/{slug}/issues/{number}", env=env)
    if not isinstance(issue, dict):
        raise SdlcError(f"GitHub gave no data for issue {number}.")
    if issue.get("pull_request"):
        raise SdlcError(f"The number {number} is a pull request, not an issue.")
    comments = github.api("GET", f"repos/{slug}/issues/{number}/comments?per_page=100", env=env) or []
    url = issue.get("html_url") or f"https://github.com/{slug}/issues/{number}"
    parts = [
        f"# Issue {number}: {issue.get('title') or ''}".rstrip(),
        "",
        f"URL: {url}",
        f"State: {issue.get('state') or 'unknown'}",
        "",
        "## Body",
        "",
        (issue.get("body") or "(empty)").strip(),
    ]
    for index, comment in enumerate(comments if isinstance(comments, list) else [], start=1):
        parts += ["", f"## Comment {index}", "", (comment.get("body") or "(empty)").strip()]
    return url, "\n".join(parts) + "\n"


# The intent PR (FR-22) ------------------------------------------------------------


def pr_body(item_id: str, intent_text: str, owner: str | None) -> str:
    gate = f"@{owner}" if owner else "the gate owner (config key codeowners.owner)"
    meta, body = parse_frontmatter(intent_text)
    fields = ""
    for key, value in meta.items():
        shown = ", ".join(value) if isinstance(value, list) else str(value)
        fields += f"- {key}: {shown}\n"
    return (
        f"Intent for work item `{item_id}`. The opieworks-sdlc intent stage opened this pull request as the bot.\n\n"
        f"Gate: {gate} decides.\n\n"
        "- Merge this pull request to accept the intent. The lane in the frontmatter is the lane of the work item.\n"
        "- To change the lane, edit `lane` in intent.md before the merge, or ask the agent to run the intent lane command.\n"
        "- Close this pull request to reject the intent. Write the reason in a comment. "
        "Then the reason goes to `sdlc/triage/`. Until then, the state is \"rejected, no reason\".\n\n"
        "No agent merges, closes, approves, or rejects this pull request.\n\n"
        "## Frontmatter of intent.md\n\n" + fields + "\n" + body.lstrip("\n")
    )


def open_intent_pr(repo: Path, item_id: str, config: dict) -> str:
    """Open the intent PR as the bot from sdlc/<id>/intent and label it sdlc:intent.

    The current branch must be the intent branch, and intent.md must have no
    problems. Return the PR URL.
    """
    branch = github.git(repo, "symbolic-ref", "--short", "HEAD").strip()
    if branch != branch_name(item_id):
        raise SdlcError(f"The current branch is {branch}. Switch to {branch_name(item_id)} first.")
    problems = check_intent(item_id, repo, config)
    if problems:
        raise SdlcError("intent.md is not complete. Run the intent check command. First problem: " + problems[0])
    path = _intent_path(repo, item_id)
    owner = (config.get("codeowners") or {}).get("owner") if isinstance(config.get("codeowners"), dict) else None
    title = f"Intent: {read_title(item_id, repo)} ({item_id})"
    return github.open_pr(repo, branch, title, pr_body(item_id, path.read_text(encoding="utf-8"), owner), config,
                          labels=[LABEL])


# Gate state (FR-22, SCN-10) -----------------------------------------------------------


@dataclass
class IntentState:
    item_id: str
    state: str                # open, accepted, rejected, or "rejected, no reason"
    pr_number: int | None
    pr_url: str | None
    reason: str | None = None
    lane: str | None = None

    def format(self) -> str:
        state = f"{REJECTED}: {self.reason}" if self.state == REJECTED and self.reason else self.state
        parts = [self.item_id, state, f"lane: {self.lane or '-'}"]
        if self.pr_url:
            parts.append(self.pr_url)
        return "  ".join(parts)


def _has_label(pull: dict) -> bool:
    return any(isinstance(label, dict) and label.get("name") == LABEL for label in pull.get("labels") or [])


def list_intent_pulls(slug: str, env: dict[str, str] | None = None, max_pages: int = 20) -> list[dict]:
    """Return every PR with the label sdlc:intent, open or closed. Read only."""
    found = []
    for page in range(1, max_pages + 1):
        data = github.api("GET", f"repos/{slug}/pulls?state=all&per_page=100&page={page}", env=env)
        if not isinstance(data, list):
            raise SdlcError("GitHub did not give a list of pull requests.")
        found += [pull for pull in data if isinstance(pull, dict) and _has_label(pull)]
        if len(data) < 100:
            break
    return found


def parse_triage(text: str, name: str) -> dict:
    """Read one intent triage record. Return {"work_item", "reason", ...}."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SdlcError(f"The triage record {name} is not valid TOML: {exc}.") from None
    item_id = data.get("work_item")
    reason = data.get("reason")
    if not isinstance(item_id, str) or not _ITEM_RE.match(item_id):
        raise SdlcError(f"The triage record {name} has no valid work_item.")
    if not isinstance(reason, str):
        reason = ""
    data["reason"] = reason.strip()
    return data


def read_triage(repo: Path, ref: str = "HEAD") -> dict[str, dict]:
    """Return the intent triage records at a git ref, by work item ID.

    It reads git objects only. It does not check out the ref.
    """
    listing = github.git(repo, "ls-tree", "-z", "--name-only", f"{ref}", "--", f"{TRIAGE_REL}/")
    records = {}
    for path in sorted(p for p in listing.split("\0") if p):
        name = path.rsplit("/", 1)[-1]
        if not (name.startswith(TRIAGE_PREFIX) and name.endswith(".toml")):
            continue
        data = parse_triage(github.git(repo, "show", f"{ref}:{path}"), path)
        records[data["work_item"]] = data
    return records


def read_lanes(repo: Path, ref: str = "HEAD") -> dict[str, str]:
    """Return the lane of each intent.md on a git ref, by work item ID."""
    listing = github.git(repo, "ls-tree", "-r", "-z", "--name-only", f"{ref}", "--", f"{WORK_REL}/")
    lanes = {}
    for path in (p for p in listing.split("\0") if p):
        parts = path.split("/")
        if len(parts) != 4 or parts[3] != INTENT_NAME or not _ITEM_RE.match(parts[2]):
            continue
        try:
            meta, _ = parse_frontmatter(github.git(repo, "show", f"{ref}:{path}"))
        except SdlcError:
            continue
        if isinstance(meta.get("lane"), str):
            lanes[parts[2]] = meta["lane"]
    return lanes


def intent_states(pulls: list[dict], triage: dict[str, dict], lanes: dict[str, str] | None = None) -> list[IntentState]:
    """Return the gate state of each work item that has an intent PR.

    The work item comes from the branch name sdlc/<id>/intent, never from the
    PR author. With more than one intent PR for a work item, the newest PR wins.
    A closed PR that is not merged is rejected. Its reason comes only from
    a triage record. With no record, the state is "rejected, no reason".
    """
    lanes = lanes or {}
    newest: dict[str, dict] = {}
    for pull in pulls:
        if not _has_label(pull):
            continue
        item_id = item_from_branch(((pull.get("head") or {}).get("ref")) or "")
        if item_id is None:
            continue
        if item_id not in newest or int(pull.get("number") or 0) > int(newest[item_id].get("number") or 0):
            newest[item_id] = pull
    states = []
    for item_id in sorted(newest):
        pull = newest[item_id]
        reason = None
        if pull.get("merged_at"):
            state = ACCEPTED
        elif pull.get("state") == "open":
            state = OPEN
        else:
            reason = (triage.get(item_id) or {}).get("reason") or None
            state = REJECTED if reason else REJECTED_NO_REASON
        states.append(IntentState(item_id, state, pull.get("number"), pull.get("html_url"), reason, lanes.get(item_id)))
    return states


def find_pull(pulls: list[dict], item_id: str) -> dict | None:
    """Return the newest intent PR of a work item, else None."""
    matches = [p for p in pulls if _has_label(p) and item_from_branch(((p.get("head") or {}).get("ref")) or "") == item_id]
    return max(matches, key=lambda p: int(p.get("number") or 0)) if matches else None


def record_reason(repo: Path, item_id: str, reason: str, pull: dict | None,
                  today: datetime.date | None = None) -> Path:
    """Write sdlc/triage/intent-<id>.toml for an intent PR that a human closed.

    Refuse when there is no intent PR, when the PR is open, or when it is merged.
    The engine does not reject an intent. It only writes down the reason of
    the human who closed the PR.
    """
    check_item_id(item_id)
    reason = (reason or "").strip()
    if not reason:
        raise SdlcError("The reason is empty. Ask the human who closed the pull request for the reason.")
    if pull is None:
        raise SdlcError(f"The work item {item_id} has no intent pull request with the label {LABEL}.")
    if pull.get("merged_at"):
        raise SdlcError(f"The intent pull request of {item_id} is merged. The intent is accepted, not rejected.")
    if pull.get("state") != "closed":
        raise SdlcError(f"The intent pull request of {item_id} is open. Only a human closes it to reject the intent.")
    data = {
        "work_item": item_id,
        "stage": STAGE,
        "decision": REJECTED,
        "reason": reason,
        "pr": pull.get("html_url") or "",
        "closed_at": pull.get("closed_at") or "",
        "recorded": (today or datetime.date.today()).isoformat(),
    }
    dest = safe_write_path(Path(repo), f"{TRIAGE_REL}/{TRIAGE_PREFIX}{item_id}.toml")
    write_if_changed(dest, dump_toml(data))
    return dest
