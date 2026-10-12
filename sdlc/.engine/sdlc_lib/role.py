"""Role specs and the role runner (FR-9, FR-94). Owner: the role module author."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import scan
from .common import DEFAULT_BOT_TOKEN_FILE, SdlcError, parse_frontmatter

CAPABILITIES = ("read", "run", "edit", "github-bot")
HUMAN_TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")

# Other names can hold a GitHub token too, for example the token that a
# GitHub MCP server reads. build_invocation also removes every variable whose
# name has a GH or GITHUB word and a TOKEN or PAT word, and every variable
# whose value has the shape of a GitHub token.
_GITHUB_NAME_WORDS = {"GH", "GITHUB"}
_TOKEN_NAME_WORDS = {"TOKEN", "PAT"}
_GITHUB_TOKEN_SHAPES = tuple(regex for name, regex in scan._LEAK_PATTERNS if name == "token:github")

# Git and ssh run an askpass program to get a login. An editor terminal can
# set one that hands out the GitHub login of the human user.
ASKPASS_VARS = ("GIT_ASKPASS", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE")
ASKPASS_PREFIX = "VSCODE_GIT_"

# gh reads a stored login from its config folder and the system keyring.
# Git can ask a credential helper or an askpass program for the stored login.
# Each role gets an empty gh config folder, no git credential helper and no
# askpass program, so the role cannot fall back to the human login.
GH_CONFIG_PREFIX = "opieworks-sdlc-gh-"
ISOLATION_ENV = {
    "GH_PROMPT_DISABLED": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "SSH_ASKPASS_REQUIRE": "never",
}
# build_invocation adds these git config entries after the GIT_CONFIG_*
# entries that the caller already has.
GIT_ISOLATION_CONFIG = (("credential.helper", ""), ("core.askPass", ""))

# Capabilities that select a flag map, weakest first. The strongest one wins.
FLAG_CAPABILITIES = ("read", "run", "edit")
BOT_CAPABILITY = "github-bot"
BOT_TOKEN_KEY = "GH_TOKEN"
# The GH_TOKEN value in a dry run. A dry run never reads the bot token file.
DRY_RUN_TOKEN = "dry-run"

REQUIRED_KEYS = ("name", "stage", "capabilities", "fresh_context", "inputs", "outputs", "stop_when")
_LIST_KEYS = ("inputs", "outputs", "stop_when")


@dataclass
class RoleSpec:
    """A parsed role spec file."""

    name: str
    stage: str
    capabilities: list[str]
    fresh_context: bool
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    stop_when: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None


def _string_list(value, key: str, where: str) -> list[str]:
    # "key:" with no items parses as an empty string. Treat it as an empty list.
    if value == "":
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SdlcError(f"The role spec {where} needs {key} as a list of strings.")
    return list(value)


def parse_role_spec(path: Path) -> RoleSpec:
    """Parse a role spec. Raise SdlcError on a bad field or an unknown capability."""
    path = Path(path)
    where = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SdlcError(f"The role spec {where} does not exist.") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise SdlcError(f"The role spec {where} cannot be read: {exc}.") from None

    data, body = parse_frontmatter(text)
    if not data:
        raise SdlcError(f"The role spec {where} has no frontmatter.")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise SdlcError(f"The role spec {where} needs the keys: {', '.join(missing)}.")

    for key in ("name", "stage"):
        if not isinstance(data[key], str) or not data[key]:
            raise SdlcError(f"The role spec {where} needs {key} as a string.")

    capabilities = data["capabilities"]
    if not isinstance(capabilities, list) or not capabilities:
        raise SdlcError(f"The role spec {where} needs capabilities as a list with one or more items.")
    for cap in capabilities:
        if cap not in CAPABILITIES:
            raise SdlcError(
                f"The role spec {where} has the unknown capability {cap!r}. "
                f"Use only: {', '.join(CAPABILITIES)}."
            )

    if not isinstance(data["fresh_context"], bool):
        raise SdlcError(f"The role spec {where} needs fresh_context as true or false.")

    lists = {key: _string_list(data[key], key, where) for key in _LIST_KEYS}

    return RoleSpec(
        name=data["name"],
        stage=data["stage"],
        capabilities=list(capabilities),
        fresh_context=data["fresh_context"],
        inputs=lists["inputs"],
        outputs=lists["outputs"],
        stop_when=lists["stop_when"],
        body=body,
        path=path,
    )


def read_bot_token(path: str | Path) -> str:
    """Return the GH_TOKEN value from a bot.env file. Raise SdlcError when absent.

    No message holds the token value.
    """
    shown = str(path)
    full = Path(path).expanduser()
    try:
        text = full.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SdlcError(
            f"The bot token file {shown} does not exist. "
            f"Create it with the line {BOT_TOKEN_KEY}=<token> or remove github-bot from the role."
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        reason = getattr(exc, "strerror", None) or type(exc).__name__
        raise SdlcError(f"The bot token file {shown} cannot be read: {reason}.") from None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key.strip() != BOT_TOKEN_KEY:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value:
            raise SdlcError(f"The bot token file {shown} has an empty {BOT_TOKEN_KEY} value.")
        return value
    raise SdlcError(f"The bot token file {shown} has no {BOT_TOKEN_KEY}= line.")


def _flag_capability(capabilities: list[str]) -> str:
    """Return the strongest of read, run, edit in capabilities.

    A spec with only github-bot gets the read map. Read is the least access
    that a role subprocess can have.
    """
    strongest = FLAG_CAPABILITIES[0]
    for cap in FLAG_CAPABILITIES:
        if cap in capabilities:
            strongest = cap
    return strongest


def _str_list(value, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SdlcError(f"{what} must be a list of strings.")
    return list(value)


def _is_human_credential(key: str, value: str) -> bool:
    """Return True when key or value can give the GitHub login of the human user."""
    if key in HUMAN_TOKEN_VARS or key in ASKPASS_VARS or key.startswith(ASKPASS_PREFIX):
        return True
    words = set(re.split(r"[^A-Z0-9]+", key.upper()))
    if words & _GITHUB_NAME_WORDS and words & _TOKEN_NAME_WORDS:
        return True
    return any(regex.search(value) for regex in _GITHUB_TOKEN_SHAPES)


def _add_git_config(env: dict[str, str]) -> None:
    """Add GIT_ISOLATION_CONFIG after the GIT_CONFIG_* entries that env has."""
    try:
        count = max(int(env.get("GIT_CONFIG_COUNT", "0")), 0)
    except ValueError:
        count = 0
    for key, value in GIT_ISOLATION_CONFIG:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)


def build_invocation(
    spec: RoleSpec,
    harness: str,
    config: dict,
    inputs: list[str] | tuple[str, ...] = (),
    base_env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Return (argv, env) for the role subprocess. See CONTRACT.md.

    With dry_run=True, the bot token file is not read. GH_TOKEN gets the
    value DRY_RUN_TOKEN, so a dry run works before the bot is set up.
    """
    runners = config.get("agent_runner")
    table = runners.get(harness) if isinstance(runners, dict) else None
    if not isinstance(table, dict):
        raise SdlcError(f"The config has no [agent_runner.{harness}] table. Add it to sdlc/config.toml.")

    where = f"[agent_runner.{harness}]"
    command = _str_list(table.get("command"), f"{where} command")
    if not command:
        raise SdlcError(f"{where} command must not be empty.")

    cap = _flag_capability(spec.capabilities)
    if cap not in table:
        raise SdlcError(f"{where} has no {cap} flag map. The role {spec.name} needs it.")
    flags = _str_list(table[cap], f"{where} {cap}")

    prompt = "\n".join([spec.body.strip("\n"), "", "Inputs:", *(str(item) for item in inputs)])
    argv = command + flags + [prompt]

    source = os.environ if base_env is None else base_env
    env = {key: value for key, value in source.items() if not _is_human_credential(key, value)}
    env.update(ISOLATION_ENV)
    _add_git_config(env)

    if BOT_CAPABILITY in spec.capabilities:
        bot = config.get("bot")
        token_file = bot.get("token_file") if isinstance(bot, dict) else None
        if dry_run:
            env[BOT_TOKEN_KEY] = DRY_RUN_TOKEN
        else:
            env[BOT_TOKEN_KEY] = read_bot_token(token_file or DEFAULT_BOT_TOKEN_FILE)

    # Make the folder last, so that an error above leaves no folder behind.
    env["GH_CONFIG_DIR"] = tempfile.mkdtemp(prefix=GH_CONFIG_PREFIX)
    return argv, env


def cleanup(env: dict[str, str]) -> None:
    """Remove the gh config folder that build_invocation made for env."""
    folder = env.get("GH_CONFIG_DIR", "")
    if Path(folder).name.startswith(GH_CONFIG_PREFIX):
        shutil.rmtree(folder, ignore_errors=True)


def execute(argv: list[str], env: dict[str, str], cwd: Path) -> int:
    """Run the role subprocess. Return its exit code."""
    if not argv:
        raise SdlcError("The role command is empty.")
    if not Path(cwd).is_dir():
        raise SdlcError(f"The folder {cwd} does not exist.")
    try:
        result = subprocess.run(argv, env=env, cwd=cwd)
    except FileNotFoundError:
        raise SdlcError(f"The command {argv[0]} was not found. Install it or fix [agent_runner] in the config.") from None
    except PermissionError:
        raise SdlcError(f"The command {argv[0]} cannot run. Check that it is executable.") from None
    return result.returncode
