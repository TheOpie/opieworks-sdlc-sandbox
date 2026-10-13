"""The deploy stage (S5): review findings, babysit rounds, canary, production. Owner: the deploy stage.

FR-60 to FR-64. The reviewer and the babysitter are roles. The engine does
every GitHub action for them as the bot:

- The engine posts the review findings as an issue comment. An issue comment
  cannot approve a pull request. The engine has no approve call (FR-10).
- The engine counts the babysit rounds, commits, pushes as the bot, and asks
  the gate owner to approve again. It stops at the round limit.
- The canary is a GitHub prerelease. The engine never makes a full release.
- Production runs only in the production workflow. Its job is bound to the
  protected environment "production". The engine starts the workflow on the
  default branch. It never approves the deployment.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import github, overlay, role
from .common import WORK_REL, SdlcError, parse_frontmatter, safe_join, safe_write_path, write_if_changed
from .verify import check_item_id, render_evidence, upstream_value

CONFIG_TABLE = "deploy"
SEVERITIES = ("important", "nit")
PASSES = ("bug", "security", "compliance")
VERDICTS = ("FINDINGS", "NO FINDINGS", "BLOCKED")
REVIEW_NAME = "review.md"
REVIEW_TEMPLATE = "templates/review.md"
BABYSITTER_SPEC = "agents/babysitter.md"
# Artifacts of earlier stages. review.md links to the ones that exist (FR-6).
UPSTREAM_NAMES = ("intent.md", "spec.md", "plan.md", "evidence.md")
DEFAULT_FALLBACK = "PR description"
REVIEW_MARKER = "<!-- opieworks-sdlc:review:{item} -->"
BABYSIT_MARKER = "<!-- opieworks-sdlc:babysit-round -->"
PRODUCTION_WORKFLOW = "sdlc-deploy-production.yml"
BUILD_INFO = "sdlc-build.json"
# Check run results that the babysitter must fix.
FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required", "startup_failure")

_FINDING = re.compile(
    r"^\s*(\d+)\.\s+(important|nit)\s*\|\s*(bug|security|compliance)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*$",
    re.IGNORECASE,
)
_VERDICT = re.compile(r"^\s*Verdict:\s*(.+?)\s*$", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(Blocked|Not reported):\s*(.*?)\s*$", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```|~~~)")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SOURCE_TITLE = re.compile(r"\bproduction ([0-9a-f]{40})\b")


# Settings -------------------------------------------------------------------


def _table(config: dict) -> dict:
    table = config.get(CONFIG_TABLE)
    return table if isinstance(table, dict) else {}


def int_setting(config: dict, key: str, minimum: int) -> int:
    """Return a whole number from the [deploy] table. Raise SdlcError when it is missing or bad."""
    value = _table(config).get(key)
    if value is None:
        raise SdlcError(f"The config has no {CONFIG_TABLE}.{key}. Add it to sdlc/config.toml. The setup output names the new keys.")
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SdlcError(f"The config key {CONFIG_TABLE}.{key} must be a whole number of {minimum} or more.")
    return value


def str_setting(config: dict, key: str, default: str | None = None) -> str:
    """Return a string from the [deploy] table."""
    value = _table(config).get(key, default)
    if not isinstance(value, str):
        raise SdlcError(f"The config key {CONFIG_TABLE}.{key} must be a string.")
    return value.strip()


def owner_login(config: dict) -> str:
    """Return the gate owner from codeowners.owner, without the @."""
    table = config.get("codeowners")
    value = table.get("owner") if isinstance(table, dict) else None
    if not isinstance(value, str) or not value.strip() or value.strip() == "@OWNER":
        raise SdlcError("The config key codeowners.owner is missing. It names the human who owns the gates.")
    return value.strip().removeprefix("@")


# Review findings (FR-60, FR-61) ----------------------------------------------


@dataclass
class Finding:
    """One finding of the reviewer."""

    severity: str
    pass_name: str
    location: str
    title: str
    detail: list[str] = field(default_factory=list)


@dataclass
class Review:
    """The parsed output of the reviewer role."""

    verdict: str
    findings: list[Finding] = field(default_factory=list)
    blocked: str = ""
    not_reported: str = ""


def parse_findings(text: str) -> Review:
    """Parse the reviewer output. See the report format in agents/reviewer.md.

    The parser reads from the last "Verdict:" line. A harness can print
    other text before the report, so the parser skips it. Fence lines are
    skipped. Raise SdlcError when the report does not follow the format.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _VERDICT.match(line)]
    if not starts:
        raise SdlcError("The reviewer output has no Verdict line. Run the reviewer again or check its role spec.")
    verdict = _VERDICT.match(lines[starts[-1]]).group(1).upper().strip(".")
    if verdict not in VERDICTS:
        raise SdlcError(f"The reviewer verdict {verdict!r} is not known. Use one of: {', '.join(VERDICTS)}.")
    review = Review(verdict=verdict)
    current = None
    for line in lines[starts[-1] + 1:]:
        if _FENCE.match(line):
            continue
        if not line.strip():
            current = None
            continue
        match = _FINDING.match(line)
        if match:
            current = Finding(match.group(2).lower(), match.group(3).lower(), match.group(4), match.group(5))
            review.findings.append(current)
            continue
        label = _LABEL.match(line)
        if label:
            current = None
            if label.group(1).lower() == "blocked":
                review.blocked = label.group(2)
            else:
                review.not_reported = label.group(2)
            continue
        if current is not None and line[:1].isspace():
            current.detail.append(line.strip())
    if verdict == "FINDINGS" and not review.findings:
        raise SdlcError("The reviewer verdict is FINDINGS, but the output has no finding line.")
    if verdict == "NO FINDINGS" and review.findings:
        raise SdlcError("The reviewer verdict is NO FINDINGS, but the output has finding lines.")
    if verdict == "BLOCKED" and not review.blocked:
        raise SdlcError("The reviewer verdict is BLOCKED, but the output has no Blocked line.")
    return review


def rank(findings: list[Finding], nit_cap: int) -> tuple[list[Finding], int]:
    """Return (the findings to show, the number of nits that the cap hides).

    Important findings come first, then nits. Inside each group the order of
    the reviewer stays. The cap keeps the first nit_cap nits.
    """
    important = [f for f in findings if f.severity == "important"]
    nits = [f for f in findings if f.severity == "nit"]
    return important + nits[:nit_cap], max(len(nits) - nit_cap, 0)


def render_findings(review: Review, nit_cap: int) -> str:
    """Return the markdown list of ranked findings."""
    if review.verdict == "BLOCKED":
        return f"The reviewer could not finish. Reason: {review.blocked}"
    shown, hidden = rank(review.findings, nit_cap)
    if not shown:
        return "No findings."
    parts = []
    for number, item in enumerate(shown, start=1):
        head = f"{number}. **{item.severity}** ({item.pass_name}) `{item.location}`: {item.title}"
        parts.append("\n".join([head, *(f"   - {line}" for line in item.detail)]))
    if hidden:
        parts.append(f"The nit cap is {nit_cap}. {hidden} more nit(s) are not shown.")
    return "\n".join(parts)


def counts(review: Review, nit_cap: int) -> str:
    shown, hidden = rank(review.findings, nit_cap)
    important = sum(1 for f in shown if f.severity == "important")
    nits = len(shown) - important
    return f"{important} important, {nits} nit(s) shown, {hidden} nit(s) over the cap of {nit_cap}"


def write_review(item_id: str, repo: Path, stage_dir: Path, review: Review, nit_cap: int,
                 links: list[str] | None = None, fallback: str | None = None) -> Path:
    """Write sdlc/work/<id>/review.md from templates/review.md (overlay first)."""
    check_item_id(item_id)
    dest = safe_write_path(repo, f"{WORK_REL}/{item_id}/{REVIEW_NAME}")
    found = list(links or []) or [name for name in UPSTREAM_NAMES if (dest.parent / name).is_file()]
    template = overlay.resolve(REVIEW_TEMPLATE, stage_dir, repo).read_text(encoding="utf-8")
    values = {
        "work_item": item_id,
        "upstream": upstream_value(dest.parent, found, fallback or DEFAULT_FALLBACK).rstrip("\n"),
        "verdict": review.verdict,
        "marker": REVIEW_MARKER.format(item=item_id),
        "counts": counts(review, nit_cap),
        "not_reported": review.not_reported or "not given",
        "findings": render_findings(review, nit_cap),
    }
    write_if_changed(dest, render_evidence(template, values))
    return dest


def comment_body(review_text: str) -> str:
    """Return the review.md text without its frontmatter, for the PR comment."""
    _data, body = parse_frontmatter(review_text)
    return body.strip("\n") + "\n"


# Bot calls --------------------------------------------------------------------


def _bot(config: dict, method: str, path: str, body: object = None) -> object:
    env = github.bot_env(config)
    try:
        return github.api(method, path, body, env=env)
    finally:
        github.cleanup(env)


def post_comment(slug: str, pr: int, body: str, config: dict, dry_run: bool = False) -> str:
    """Post body as an issue comment on the PR as the bot. Return the URL.

    An issue comment cannot approve. With dry_run, post nothing and do not
    read the bot token file. Return a line that names the call.
    """
    path = f"repos/{slug}/issues/{int(pr)}/comments"
    if dry_run:
        return f"dry run: POST {path} as the bot. Nothing was posted."
    data = _bot(config, "POST", path, {"body": body})
    return data["html_url"]


# Babysitter (FR-62) -----------------------------------------------------------


@dataclass
class BabysitResult:
    round: int
    limit: int
    lines: list[str] = field(default_factory=list)
    pushed: bool = False
    handoff: bool = False


def _git(repo: Path, *args: str) -> str:
    return github.git(repo, *args)


def _is_bot(user: dict | None, bot: str) -> bool:
    return bool(user) and str(user.get("login", "")).casefold() == bot.casefold()


def rounds_done(comments: list[dict], bot: str) -> int:
    """Count the babysit round comments of the bot."""
    return sum(1 for c in comments if _is_bot(c.get("user"), bot) and BABYSIT_MARKER in (c.get("body") or ""))


def feedback_text(pr: dict, comments: list[dict], review_comments: list[dict], reviews: list[dict],
                  checks: list[dict], round_number: int, limit: int) -> tuple[str, int]:
    """Return (the babysitter input markdown, the number of items to fix)."""
    items = []
    for c in comments:
        body = (c.get("body") or "").strip()
        if body and BABYSIT_MARKER not in body:
            items.append(f"### Comment by {c.get('user', {}).get('login', 'unknown')}\n\n{body}\n")
    for r in reviews:
        body = (r.get("body") or "").strip()
        if body and r.get("state") in ("CHANGES_REQUESTED", "COMMENTED"):
            items.append(f"### Review ({r.get('state')}) by {r.get('user', {}).get('login', 'unknown')}\n\n{body}\n")
    for c in review_comments:
        where = f"{c.get('path', '?')}:{c.get('line') or c.get('original_line') or '?'}"
        items.append(f"### Line comment at `{where}` by {c.get('user', {}).get('login', 'unknown')}\n\n{(c.get('body') or '').strip()}\n")
    failed = [c for c in checks if c.get("status") == "completed" and c.get("conclusion") in FAILED_CONCLUSIONS]
    for c in failed:
        output = c.get("output") or {}
        summary = (output.get("title") or "") + ("\n\n" + output["summary"] if output.get("summary") else "")
        items.append(f"### Failed check `{c.get('name')}` ({c.get('conclusion')})\n\n{summary.strip() or 'No summary.'}\n"
                     f"Details: {c.get('html_url') or c.get('details_url') or 'none'}\n")
    head = (
        f"# Babysit input: PR #{pr['number']}, round {round_number} of {limit}\n\n"
        f"Title: {pr.get('title', '')}\n\n## PR description\n\n{(pr.get('body') or 'No description.').strip()}\n\n"
        f"## Comments and failed checks\n\n"
    )
    return head + ("\n".join(items) if items else "Nothing to fix.\n"), len(items)


def babysit(repo: Path, stage_dir: Path, pr_number: int, harness: str, config: dict,
            dry_run: bool = False) -> BabysitResult:
    """Run one babysit round on a PR. See CONTRACT.md, section Stage: deploy."""
    limit = int_setting(config, "babysit_rounds", 1)
    owner = owner_login(config)
    bot = github.bot_login(config)
    slug = github.origin_slug(repo)
    get = (lambda path: github.api("GET", path)) if dry_run else (lambda path: _bot(config, "GET", path))
    pr = get(f"repos/{slug}/pulls/{int(pr_number)}")
    if pr.get("state") != "open":
        raise SdlcError(f"PR #{pr_number} is not open.")
    if (pr.get("head", {}).get("repo") or {}).get("full_name", "").casefold() != slug.casefold():
        raise SdlcError(f"PR #{pr_number} comes from a fork. The babysitter works only on branches of {slug}.")
    head_ref, head_sha = pr["head"]["ref"], pr["head"]["sha"]
    branch = _git(repo, "symbolic-ref", "--short", "HEAD").strip()
    if branch != head_ref:
        raise SdlcError(f"Check out the PR branch {head_ref} first. The current branch is {branch}.")
    if _git(repo, "status", "--porcelain").strip():
        raise SdlcError("The work tree has uncommitted changes. Commit or remove them first.")
    if _git(repo, "rev-parse", "HEAD").strip() != head_sha:
        raise SdlcError(f"The local branch is not at the PR head {head_sha[:7]}. Pull or push it first.")

    comments = get(f"repos/{slug}/issues/{int(pr_number)}/comments?per_page=100") or []
    done = rounds_done(comments, bot)
    result = BabysitResult(round=done + 1, limit=limit)
    if done >= limit:
        result.round = done
        result.handoff = True
        result.lines.append(f"handoff: {done} of {limit} rounds are done. The babysitter stops. "
                            f"{owner} decides the next step.")
        return result
    review_comments = get(f"repos/{slug}/pulls/{int(pr_number)}/comments?per_page=100") or []
    reviews = get(f"repos/{slug}/pulls/{int(pr_number)}/reviews?per_page=100") or []
    checks = (get(f"repos/{slug}/commits/{head_sha}/check-runs?per_page=100") or {}).get("check_runs", [])
    text, count = feedback_text(pr, comments, review_comments, reviews, checks, result.round, limit)
    if count == 0:
        result.handoff = True
        result.lines.append(f"handoff: PR #{pr_number} has no comment and no failed check. Nothing to fix. "
                            f"{owner} reviews and approves.")
        return result

    spec = role.parse_role_spec(overlay.resolve(BABYSITTER_SPEC, stage_dir, repo))
    with tempfile.TemporaryDirectory(prefix="opieworks-sdlc-babysit-") as tmp:
        feedback = Path(tmp) / "babysit-input.md"
        feedback.write_text(text, encoding="utf-8")
        argv, env = role.build_invocation(spec, harness, config, inputs=[str(feedback)], dry_run=dry_run)
        try:
            if dry_run:
                result.lines.append(f"dry run: round {result.round} of {limit}, {count} item(s) to fix.")
                result.lines.append("dry run: role argv: " + " ".join(argv[:-1]) + " <prompt>")
                result.lines.append(f"dry run: then commit, push {head_ref} as the bot, and post the round comment.")
                return result
            code = role.execute(argv, env, repo)
        finally:
            role.cleanup(env)
    result.lines.append(f"babysitter exit code: {code}")
    if _git(repo, "symbolic-ref", "--short", "HEAD").strip() != head_ref:
        raise SdlcError(f"The babysitter left the branch {head_ref}. Check the repo. The engine pushed nothing.")
    if _git(repo, "status", "--porcelain").strip():
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"Babysit round {result.round} of {limit} for PR #{pr_number}")
    new_sha = _git(repo, "rev-parse", "HEAD").strip()
    if new_sha == head_sha:
        result.handoff = True
        result.lines.append(f"handoff: the babysitter made no change. {owner} decides the next step.")
        return result
    github.push(repo, head_ref, config)
    result.pushed = True
    last = result.round >= limit
    body = (
        f"{BABYSIT_MARKER}\n@{owner} Babysit round {result.round} of {limit}. The bot pushed {new_sha[:7]} "
        f"to fix {count} item(s). The push dismissed earlier approvals. Review the change and approve again.\n"
        + ("\nThis was the last round. The babysitter stops here and hands off to you.\n" if last else "")
    )
    url = post_comment(slug, pr_number, body, config)
    result.handoff = last
    result.lines.append(f"pushed {head_ref} at {new_sha[:7]} as the bot")
    result.lines.append(f"asked {owner} to approve again: {url}")
    if last:
        result.lines.append(f"handoff: this was round {limit} of {limit}. The babysitter stops.")
    return result


# Canary (FR-63) -----------------------------------------------------------------


def canary_payload(item_id: str, tag: str, target: str) -> dict:
    """Return the release body for a canary. It is always a prerelease."""
    check_item_id(item_id)
    if not _TAG.match(tag) or ".." in tag or tag.endswith(".lock"):
        raise SdlcError(f"The tag {tag!r} is not valid. Use letters, digits, '.', '_', and '-'.")
    return {
        "tag_name": tag,
        "target_commitish": target,
        "name": f"Canary {tag}",
        "body": (f"Canary for work item {item_id}. This is a prerelease, not production.\n\n"
                 "Production runs only in the production workflow, after the gate owner approves the deployment."),
        "draft": False,
        "prerelease": True,
        "make_latest": "false",
    }


def canary(repo: Path, item_id: str, tag: str, target: str | None, config: dict, dry_run: bool = False) -> str:
    """Create a canary prerelease as the bot. Return the release URL or the dry run line."""
    slug = github.origin_slug(repo)
    if target is None:
        target = github.default_branch(slug) if dry_run else _bot(config, "GET", f"repos/{slug}")["default_branch"]
    payload = canary_payload(item_id, tag, target)
    if dry_run:
        return f"dry run: POST repos/{slug}/releases as the bot: " + json.dumps(payload, sort_keys=True)
    data = _bot(config, "POST", f"repos/{slug}/releases", payload)
    if data.get("prerelease") is not True:
        raise SdlcError(f"GitHub made release {data.get('html_url')} without the prerelease flag. Check it now.")
    return data["html_url"]


# Production and rollback (FR-63, FR-64) ------------------------------------------


@dataclass
class ProductionRun:
    source: str
    url: str | None
    lines: list[str] = field(default_factory=list)


def _resolve_sha(slug: str, ref: str, config: dict, dry_run: bool) -> str:
    if _SHA.match(ref):
        return ref
    caller = (lambda p: github.api("GET", p)) if dry_run else (lambda p: _bot(config, "GET", p))
    return caller(f"repos/{slug}/commits/{ref}")["sha"]


def production(repo: Path, source: str | None, item_id: str | None, config: dict, dry_run: bool = False,
               sleep=time.sleep, polls: int = 10) -> ProductionRun:
    """Start the production workflow on the default branch as the bot.

    The engine always dispatches on the default branch. It has no option for
    another branch. The run waits for the gate owner in the environment.
    """
    if item_id is not None:
        check_item_id(item_id)
    owner = owner_login(config)
    slug = github.origin_slug(repo)
    caller = (lambda m, p, b=None: github.api(m, p, b)) if dry_run else (lambda m, p, b=None: _bot(config, m, p, b))
    branch = caller("GET", f"repos/{slug}")["default_branch"]
    sha = _resolve_sha(slug, source or branch, config, dry_run)
    inputs = {"source_sha": sha, "work_item": item_id or "none"}
    path = f"repos/{slug}/actions/workflows/{PRODUCTION_WORKFLOW}"
    run = ProductionRun(source=sha, url=None)
    if dry_run:
        run.lines.append(f"dry run: POST {path}/dispatches as the bot: " + json.dumps({"ref": branch, "inputs": inputs}, sort_keys=True))
        return run
    before = {r["id"] for r in (caller("GET", f"{path}/runs?per_page=20") or {}).get("workflow_runs", [])}
    caller("POST", f"{path}/dispatches", {"ref": branch, "inputs": inputs})
    run.lines.append(f"started {PRODUCTION_WORKFLOW} on {branch} for source {sha[:7]} as the bot")
    for _ in range(polls):
        sleep(3)
        runs = (caller("GET", f"{path}/runs?per_page=20") or {}).get("workflow_runs", [])
        new = [r for r in runs if r["id"] not in before]
        if new:
            run.url = new[0]["html_url"]
            break
    run.lines.append(f"run: {run.url or 'not found yet. See the Actions tab'}")
    run.lines.append(f"The run waits for {owner} to approve the production environment. The bot does not approve it.")
    return run


def history(repo: Path, config: dict, limit: int = 10, dry_run: bool = False) -> list[dict]:
    """Return the recent production runs, newest first, with the source commit of each."""
    slug = github.origin_slug(repo)
    caller = (lambda p: github.api("GET", p)) if dry_run else (lambda p: _bot(config, "GET", p))
    data = caller(f"repos/{slug}/actions/workflows/{PRODUCTION_WORKFLOW}/runs?per_page={int(limit)}") or {}
    rows = []
    for r in data.get("workflow_runs", []):
        match = _SOURCE_TITLE.search(r.get("display_title") or "")
        rows.append({
            "id": r.get("id"), "source": match.group(1) if match else None,
            "status": r.get("status"), "conclusion": r.get("conclusion"),
            "created": r.get("created_at"), "url": r.get("html_url"),
            "branch": r.get("head_branch"),
        })
    return rows


def rollback_source(rows: list[dict]) -> tuple[str, str]:
    """Return (current source, prior source) from successful production runs, newest first."""
    done = [r for r in rows if r["conclusion"] == "success" and r["source"]]
    if not done:
        raise SdlcError("No successful production run is in the history. There is nothing to roll back.")
    current = done[0]["source"]
    for r in done[1:]:
        if r["source"] != current:
            return current, r["source"]
    raise SdlcError("The history has no earlier successful build with a different source. Give --to <sha>.")


# Docs build for the production workflow (CI) -------------------------------------


def build_docs(repo: Path, config: dict, source: str, out: Path) -> dict:
    """Build the docs of the source commit into out. Runs in the production job.

    The source must be in the history of HEAD, the default branch commit that
    the workflow runs on. So production deploys only reviewed commits, also
    for a rollback.
    """
    repo = Path(repo)
    out = Path(out)
    docs_dir = str_setting(config, "docs_dir", "docs") or "docs"
    command = str_setting(config, "docs_build", "")
    if out.exists() and any(out.iterdir()):
        raise SdlcError(f"The output folder {out} is not empty.")
    head = _git(repo, "rev-parse", "HEAD").strip()
    try:
        sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{source or head}^{{commit}}").strip()
    except SdlcError:
        raise SdlcError(f"The source {source!r} is not a commit in this clone. Fetch the full history.") from None
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, head], cwd=repo).returncode != 0:
        raise SdlcError(f"The source {sha[:7]} is not in the history of the default branch. "
                        "Production deploys only commits that a human merged.")
    tmp = Path(tempfile.mkdtemp(prefix="opieworks-sdlc-docs-"))
    tree = tmp / "src"
    try:
        _git(repo, "worktree", "add", "--detach", "--quiet", str(tree), sha)
        if command:
            done = subprocess.run(command, shell=True, cwd=tree)
            if done.returncode != 0:
                raise SdlcError(f"The docs build command failed with exit code {done.returncode}.")
        site = safe_join(tree, docs_dir)
        if not site.is_dir():
            raise SdlcError(f"The source {sha[:7]} has no folder {docs_dir}. Set deploy.docs_dir in sdlc/config.toml.")
        if out.exists():
            out.rmdir()
        shutil.copytree(site, out, symlinks=True)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
    info = {"source": sha, "default_branch_head": head, "docs_dir": docs_dir, "older_than_head": sha != head}
    (out / BUILD_INFO).write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return info
