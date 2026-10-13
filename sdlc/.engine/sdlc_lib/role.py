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

# D22: the role process gets only allowlisted names from the parent env. The
# parent env can hold many keys for other services. A role could copy one into
# evidence that goes into a commit. Each name here is not a secret.
BASE_ENV_ALLOWLIST = (
    "PATH",  # The harness and the verify tools are found through PATH.
    "HOME",  # The harness reads its config and its login from the home folder.
    "USER",  # Some tools and the system keychain lookup use the user name.
    "LOGNAME",  # The same as USER on some systems.
    "SHELL",  # The harness runs commands in this shell.
    "TMPDIR",  # The temp folder. macOS sets a per-user folder here.
    "TMP",  # The temp folder name that some tools read.
    "TEMP",  # The temp folder name that some tools read.
    "LANG",  # The text encoding of tool output.
    "TERM",  # The terminal type for tool output.
    "COLORTERM",  # The color support of the terminal.
    "TZ",  # The time zone for times in logs and evidence.
    "NO_COLOR",  # Tools give output without color codes when it is set.
    # The isolation adds git config entries after the entries of the caller.
    # Keep the caller entries, for example safe.directory in CI.
    "GIT_CONFIG_COUNT",
)
# Names that start with these parts pass too. LC_* sets the locale. The
# GIT_CONFIG_* parts are the caller entries that GIT_CONFIG_COUNT counts.
BASE_ENV_PREFIXES = ("LC_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")

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


def allowed_env_names(harness: str, config: dict) -> set[str]:
    """Return the env names that a role process can get from the parent env.

    The names are BASE_ENV_ALLOWLIST, the env list of [agent_runner.<harness>],
    and the env list of [roles]. Names with a BASE_ENV_PREFIXES part also pass.
    """
    names = set(BASE_ENV_ALLOWLIST)
    runners = config.get("agent_runner")
    table = runners.get(harness) if isinstance(runners, dict) else None
    if isinstance(table, dict) and "env" in table:
        names.update(_str_list(table["env"], f"[agent_runner.{harness}] env"))
    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise SdlcError("[roles] must be a table in sdlc/config.toml.")
    if "env" in roles:
        names.update(_str_list(roles["env"], "[roles] env"))
    return names


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
    task: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return (argv, env) for the role subprocess. See CONTRACT.md.

    With dry_run=True, the bot token file is not read. GH_TOKEN gets the
    value DRY_RUN_TOKEN, so a dry run works before the bot is set up.
    A task text goes in the prompt after the spec body, under a "Task:" line.
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

    task_lines = ["Task:", task.strip("\n"), ""] if task else []
    prompt = "\n".join([spec.body.strip("\n"), "", *task_lines, "Inputs:", *(str(item) for item in inputs)])
    argv = command + flags + [prompt]

    # The allowlist comes first. A GitHub credential is removed also when the
    # config allows its name.
    names = allowed_env_names(harness, config)
    source = os.environ if base_env is None else base_env
    env = {
        key: value for key, value in source.items()
        if (key in names or key.startswith(BASE_ENV_PREFIXES)) and not _is_human_credential(key, value)
    }
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


# No-edit check (D20) -------------------------------------------------------------
#
# A role without the edit capability must not change the repo. The harness
# flags cannot always block a write (a shell can write files), so the engine
# compares the repo state before and after the role. It detects only. It
# never reverts, because the human decides what to keep.

NO_EDIT_EXIT = 3
NO_EDIT_EXEMPT = "sdlc/work/"
EDIT_CAPABILITY = "edit"


class NoEditViolation(SdlcError):
    """A role without the edit capability changed the repo."""


def needs_no_edit_check(capabilities: list[str]) -> bool:
    return EDIT_CAPABILITY not in capabilities


def _git_out(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise SdlcError(f"The no-edit check cannot read the repo state: git {args[0]} failed: {detail[-1] if detail else 'no output'}")
    return result.stdout


def _content_hash(path: Path) -> str:
    import hashlib

    try:
        if path.is_symlink():
            return "link:" + os.readlink(path)
        if path.is_dir():
            return "dir"
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"


def snapshot(repo: Path) -> dict:
    """Return the HEAD commit and the status and content hash of each dirty path.

    Ignored files are not in the snapshot.
    """
    repo = Path(repo)
    # A repo with no commit has no HEAD. Then the head part is empty.
    found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo, capture_output=True)
    head = found.stdout.decode("ascii").strip() if found.returncode == 0 else ""
    out = _git_out(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths = {}
    records = out.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index].decode("utf-8", errors="surrogateescape")
        index += 1
        if not record:
            continue
        status, path = record[:2], record[3:]
        if status[0] in "RC" and index < len(records):
            old = records[index].decode("utf-8", errors="surrogateescape")
            index += 1
            paths[old] = ("R-", "")
        paths[path] = (status, _content_hash(repo / path))
    return {"head": head, "paths": paths}


def changed_paths(before: dict, after: dict) -> list[str]:
    """Return the paths that differ between two snapshots, outside sdlc/work/."""
    names = set(before["paths"]) | set(after["paths"])
    changed = sorted(
        name for name in names
        if before["paths"].get(name) != after["paths"].get(name) and not name.startswith(NO_EDIT_EXEMPT)
    )
    if before["head"] != after["head"]:
        changed.insert(0, "HEAD (the role made a commit or moved the branch)")
    return changed


def execute_checked(spec: RoleSpec, argv: list[str], env: dict[str, str], cwd: Path) -> int:
    """Run the role. For a role without edit, raise NoEditViolation when it changed the repo."""
    if not needs_no_edit_check(spec.capabilities):
        return execute(argv, env, cwd)
    before = snapshot(cwd)
    code = execute(argv, env, cwd)
    changed = changed_paths(before, snapshot(cwd))
    if changed:
        listing = "".join(f"\n  {name}" for name in changed)
        raise NoEditViolation(
            f"The role {spec.name} has no edit capability, but it changed the repo outside {NO_EDIT_EXEMPT}. "
            f"Review or discard these changes. The engine did not undo them.{listing}",
            code=NO_EDIT_EXIT,
        )
    return code
