"""Open the setup PR as the bot (FR-8, FR-93). Owner: the install module author.

Bootstrap order (spec section 9): setup opens the setup PR as the bot, a
human merges it, then gates apply makes the rules, then check-bot.
"""

from __future__ import annotations

from pathlib import Path

from . import common, github, install, role
from .common import DEFAULT_BOT_TOKEN_FILE, SdlcError

BRANCH_PREFIX = "sdlc/setup/"


def _ok(repo: Path, *args: str) -> bool:
    return github._run([github.GIT, *args], cwd=repo).returncode == 0


def _body(stage_id: str, report: install.SetupReport) -> str:
    lines = [
        f"This PR installs the opieworks-sdlc `{stage_id}` stage. The opieworks-sdlc bot opened it.",
        "",
        "A human reviews and merges this PR. The bot never merges or approves.",
        "After the merge, a repo admin runs `python3 sdlc/.engine/sdlc.py gates apply`, "
        "then `python3 sdlc/.engine/sdlc.py check-bot`.",
        "",
        "Changed files:",
        "",
    ]
    lines += [f"- `{rel}`" for rel in report.written + report.removed]
    if report.config_created:
        lines.append(f"- `{common.CONFIG_REL}` (created)")
    if report.new_config_keys:
        lines += ["", "New config keys that `sdlc/config.toml` does not have:", ""]
        lines += [f"- `{key}`" for key in report.new_config_keys]
    return "\n".join(lines) + "\n"


def open_setup_pr(stage_dir: Path, repo: Path) -> install.SetupReport:
    """Run setup on a new branch, commit, push as the bot, and open the PR.

    With no changes, return the report with pr_url None and leave no branch.
    """
    repo = Path(repo)
    stage = common.load_stage(stage_dir)
    config = common.load_config(repo, stage["config_defaults"])
    github.bot_login(config)
    bot = config.get("bot", {})
    role.read_bot_token(bot.get("token_file") or DEFAULT_BOT_TOKEN_FILE)
    github.origin_slug(repo)
    if github.git(repo, "status", "--porcelain").strip():
        raise SdlcError("The repo has changes that are not committed. Commit or stash them, then run setup again.")
    start = github.git(repo, "symbolic-ref", "--short", "HEAD").strip()
    branch = BRANCH_PREFIX + stage["id"]
    if _ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        raise SdlcError(f"The branch {branch} exists. Merge or close its PR and delete the branch, then run setup again.")

    github.git(repo, "switch", "-q", "-c", branch)
    pushed = False
    try:
        report = install.setup(stage_dir, repo)
        if not github.git(repo, "status", "--porcelain").strip():
            return report
        github.git(repo, "add", "-A")
        github.git(repo, "commit", "-q", "-m", f"Set up the opieworks-sdlc {stage['id']} stage")
        github.push(repo, branch, config)
        pushed = True
        report.pr_url = github.open_pr(
            repo, branch, f"Set up the opieworks-sdlc {stage['id']} stage", _body(stage["id"], report), config,
            base=start,
        )
        return report
    except SdlcError as exc:
        if pushed:
            raise SdlcError(
                f"{exc.message} The bot pushed the branch {branch}. Open the PR with sdlc.py pr."
            ) from None
        raise
    finally:
        # The tree was clean at the start, so this removes only setup output.
        github.git(repo, "reset", "-q", "--hard")
        github.git(repo, "clean", "-fdq")
        github.git(repo, "switch", "-q", start)
        if not pushed:
            github.git(repo, "branch", "-q", "-D", branch)
