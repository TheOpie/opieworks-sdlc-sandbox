"""GitHub calls for the engine (FR-8, FR-94). Owner: the github module author.

Two kinds of calls:

- Human calls use the gh login of the person who runs the command. Only
  gates and check-bot use them. They need repo admin rights.
- Bot calls use the bot token from the bot token file. The engine gives the
  token only to its own child processes, with no human token in the env and
  an empty gh config folder. Git gets the token from a per-command credential
  helper that reads an env variable, so the token is never in argv.

The engine has no merge and no approve call. Agents never pass a gate (FR-10).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import role
from .common import DEFAULT_BOT_TOKEN_FILE, SdlcError

GH = "gh"
GIT = "git"

# The env variable that the git credential helper reads. The name has no
# GH or GITHUB word, so it is clear that only this helper uses it.
BOT_SECRET_ENV = "OPIEWORKS_SDLC_BOT_SECRET"
# A git credential helper that gives the bot login for a "get" request.
CREDENTIAL_HELPER = (
    "!f() { test \"$1\" = get || exit 0; echo username=x-access-token; "
    "echo \"password=$" + BOT_SECRET_ENV + "\"; }; f"
)
# Reset the helper list first, so that no stored human login can answer.
BOT_GIT_CONFIG = (
    ("credential.helper", ""),
    ("credential.helper", CREDENTIAL_HELPER),
    ("core.askPass", ""),
)

_REMOTE = re.compile(r"^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\)")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class NotFound(SdlcError):
    """The GitHub API gave HTTP 404."""


def _run(argv: list[str], env: dict[str, str] | None = None, cwd: Path | None = None,
         input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run one command and capture its output. Tests replace this function."""
    try:
        return subprocess.run(argv, env=env, cwd=cwd, input=input_text, capture_output=True, text=True)
    except FileNotFoundError:
        raise SdlcError(f"The command {argv[0]} was not found. Install it and run the command again.") from None


# Repo facts ------------------------------------------------------------------


def parse_remote(url: str) -> tuple[str, str]:
    """Return (owner, name) for an HTTPS github.com remote URL."""
    match = _REMOTE.match(url.strip())
    if not match:
        raise SdlcError(
            "The origin remote must be an HTTPS github.com URL, for example "
            "https://github.com/owner/name.git. The bot credential works only on HTTPS."
        )
    return match.group(1), match.group(2)


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run git in repo. Return stdout. Raise SdlcError on failure."""
    result = _run([GIT, *args], env=env, cwd=repo)
    if result.returncode != 0:
        # Keep the last lines. A hook gives its reason before the final git line.
        text = _ANSI.sub("", (result.stderr or result.stdout or "").strip())
        detail = [line.strip() for line in text.splitlines() if line.strip()][-5:]
        raise SdlcError(f"git {args[0]} failed: {' | '.join(detail) if detail else 'no output'}")
    return result.stdout


def origin_slug(repo: Path) -> str:
    """Return "owner/name" of the origin remote of repo."""
    owner, name = parse_remote(git(repo, "remote", "get-url", "origin").strip())
    return f"{owner}/{name}"


def bot_login(config: dict) -> str:
    """Return the bot login from the config key bot.login."""
    bot = config.get("bot")
    login = bot.get("login") if isinstance(bot, dict) else None
    if not isinstance(login, str) or not _LOGIN.match(login):
        raise SdlcError('The config key bot.login is missing or not valid. Add [bot] login = "<bot user>" to sdlc/config.toml.')
    return login


# Environments ----------------------------------------------------------------


def bot_env(config: dict, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the env for a bot child process. The caller must call cleanup(env).

    The env has no human token and no askpass program. GH_TOKEN and the
    credential helper variable hold the bot token. GH_CONFIG_DIR is a new
    empty folder, so gh cannot find the stored human login.
    """
    bot = config.get("bot")
    token_file = bot.get("token_file") if isinstance(bot, dict) else None
    token = role.read_bot_token(token_file or DEFAULT_BOT_TOKEN_FILE)
    source = os.environ if base_env is None else base_env
    env = {key: value for key, value in source.items() if not role._is_human_credential(key, value)}
    env.update(role.ISOLATION_ENV)
    try:
        count = max(int(env.get("GIT_CONFIG_COUNT", "0")), 0)
    except ValueError:
        count = 0
    for key, value in BOT_GIT_CONFIG:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    env[role.BOT_TOKEN_KEY] = token
    env[BOT_SECRET_ENV] = token
    env["GH_CONFIG_DIR"] = tempfile.mkdtemp(prefix=role.GH_CONFIG_PREFIX)
    return env


def cleanup(env: dict[str, str]) -> None:
    """Remove the gh config folder of a bot env."""
    role.cleanup(env)


# API ------------------------------------------------------------------------


def api(method: str, path: str, body: object = None, env: dict[str, str] | None = None) -> object:
    """Call the GitHub REST API through gh. Return the parsed JSON, or None.

    With env=None, gh uses the login of the person who runs the command.
    Raise NotFound on HTTP 404 and SdlcError on other failures.
    """
    argv = [GH, "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
    input_text = None
    if body is not None:
        argv += ["--input", "-"]
        input_text = json.dumps(body)
    result = _run(argv, env=env, input_text=input_text)
    if result.returncode != 0:
        text = (result.stderr or "").strip()
        status = _HTTP_STATUS.search(text)
        first = text.splitlines()[0] if text else "no output"
        message = f"GitHub API {method} {path} failed: {first}"
        if status and status.group(1) == "404":
            raise NotFound(message)
        raise SdlcError(message)
    out = result.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise SdlcError(f"GitHub API {method} {path} did not give JSON.") from None


# Bot actions ----------------------------------------------------------------


def default_branch(slug: str, env: dict[str, str] | None = None) -> str:
    data = api("GET", f"repos/{slug}", env=env)
    return data["default_branch"]


def push(repo: Path, branch: str, config: dict) -> None:
    """Push branch to origin as the bot. Refuse the default branch.

    Agents change the default branch only through a PR that a human merges.
    """
    slug = origin_slug(repo)
    env = bot_env(config)
    try:
        if branch == default_branch(slug, env=env):
            raise SdlcError(f"The bot does not push to the default branch {branch}. Push a work branch and open a PR.")
        git(repo, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}", env=env)
    finally:
        cleanup(env)


def open_pr(repo: Path, head: str, title: str, body: str, config: dict, base: str | None = None) -> str:
    """Open a PR from head as the bot. Return the PR URL."""
    slug = origin_slug(repo)
    env = bot_env(config)
    try:
        base = base or default_branch(slug, env=env)
        data = api(
            "POST", f"repos/{slug}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
            env=env,
        )
    finally:
        cleanup(env)
    return data["html_url"]


def which_git_login(repo: Path, config: dict) -> str:
    """Return the git credential user name that a bot push uses for origin.

    The value is "x-access-token" when the bot helper answers. It never
    returns the password.
    """
    url = git(repo, "remote", "get-url", "origin").strip()
    parse_remote(url)
    env = bot_env(config)
    try:
        result = _run([GIT, "credential", "fill"], env=env, cwd=repo,
                      input_text=f"url={url}\n\n")
    finally:
        cleanup(env)
    for line in (result.stdout or "").splitlines():
        if line.startswith("username="):
            return line.partition("=")[2]
    return ""
