"""Shared helpers for the opieworks-sdlc engine.

Every module imports from here. See CONTRACT.md for the public interface.
"""

from __future__ import annotations

import copy
import os
import re
import stat
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath

ENGINE_DIR = Path(__file__).resolve().parent.parent

OVERLAY_DIR = "sdlc"
# The overlay is only these folders. Setup never writes under them.
OVERLAY_PREFIXES = ("sdlc/templates", "sdlc/agents")
CONFIG_REL = "sdlc/config.toml"
ENGINE_REL = "sdlc/.engine"
WORK_REL = "sdlc/work"

DEFAULT_BOT_TOKEN_FILE = "~/.config/opieworks-sdlc/bot.env"
DEFAULT_LEAK_DENYLIST = "~/.config/opieworks-sdlc/leak-denylist.txt"
LEAK_DENYLIST_ENV = "OPIEWORKS_SDLC_LEAK_DENYLIST"

CORE_DEFAULTS = {"bot": {"token_file": DEFAULT_BOT_TOKEN_FILE}}

INSTALL_KINDS = ("managed", "user")
# A .git folder holds the history in compressed objects that a scan cannot read.
EXCLUDED_DIRS = ("tests", "__pycache__", ".git")
# Files that the operating system makes. The engine copy and export skip them.
JUNK_FILES = (".DS_Store", "Thumbs.db")

_STAGE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")

BOM = "\ufeff"

MARKER_STYLES = {
    "md": ("<!-- opieworks-sdlc:{stage}:begin -->", "<!-- opieworks-sdlc:{stage}:end -->"),
    "hash": ("# opieworks-sdlc:{stage}:begin", "# opieworks-sdlc:{stage}:end"),
}


class SdlcError(Exception):
    """A failure that the CLI reports as one line with an exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.message = message
        self.code = code


# Paths ---------------------------------------------------------------------


def repo_root(explicit: str | os.PathLike | None = None, start: Path | None = None) -> Path:
    """Return the target repo root.

    With an explicit path, return it resolved. The folder must exist.
    Without one, walk up from start (default: the current folder) to the
    first folder that holds a .git entry.
    """
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise SdlcError(f"The repo folder {explicit} does not exist.")
        return path
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / ".git").exists():
            return folder
    raise SdlcError("No git repo found. Run the command in a repo or use --repo.")


def safe_join(base: str | os.PathLike, relpath: str) -> Path:
    """Join base and relpath. Reject a relpath that can leave base.

    The check is lexical. It rejects empty paths, absolute paths, drive
    letters, backslashes, and any ".." part.
    """
    if not isinstance(relpath, str) or not relpath.strip():
        raise SdlcError("The path is empty.")
    if "\\" in relpath or "\x00" in relpath:
        raise SdlcError(f"The path {relpath!r} has a character that is not allowed.")
    posix = PurePosixPath(relpath)
    if posix.is_absolute() or PureWindowsPath(relpath).drive:
        raise SdlcError(f"The path {relpath!r} must be relative.")
    if ".." in posix.parts:
        raise SdlcError(f"The path {relpath!r} must not contain '..'.")
    return Path(base).joinpath(*posix.parts)


def safe_write_path(base: str | os.PathLike, relpath: str, allow_leaf_link: bool = False) -> Path:
    """Return safe_join(base, relpath) for a write. Refuse a path through a symlink.

    Each part of relpath that exists below base must not be a symlink, so a
    write cannot leave base. With allow_leaf_link=True, the last part may be
    a symlink. Use that only when the caller does not write a symlinked leaf.
    """
    path = safe_join(base, relpath)
    parts = PurePosixPath(relpath).parts
    current = Path(base)
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except (FileNotFoundError, NotADirectoryError):
            break
        if stat.S_ISLNK(mode) and not (allow_leaf_link and index == len(parts) - 1):
            shown = "/".join(parts[: index + 1])
            raise SdlcError(f"{shown} is a symlink. The engine does not write through a symlink.")
    return path


def iter_files(root: str | os.PathLike, exclude_dirs=EXCLUDED_DIRS, followlinks: bool = True) -> list[Path]:
    """Return every file under root as a sorted list of relative paths.

    The walk follows symlinks unless followlinks is False. It skips folders
    whose name is in exclude_dirs at any depth, .pyc files, and JUNK_FILES.
    """
    root = Path(root)
    found = []
    for folder, dirs, files in os.walk(root, followlinks=followlinks):
        dirs[:] = sorted(d for d in dirs if d not in exclude_dirs)
        for name in files:
            if name.endswith(".pyc") or name in JUNK_FILES:
                continue
            found.append((Path(folder) / name).relative_to(root))
    return sorted(found, key=lambda p: p.as_posix())


def write_if_changed(path: str | os.PathLike, data: str | bytes) -> bool:
    """Write data to path when the content differs. Return True on a write.

    Text is written as UTF-8 with no newline translation. Parent folders
    are made as needed.
    """
    path = Path(path)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if path.is_file() and path.read_bytes() == raw:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True


# TOML and config -----------------------------------------------------------


def load_toml(path: str | os.PathLike) -> dict:
    """Read a TOML file. Raise SdlcError when it is missing or not valid."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise SdlcError(f"The file {path} does not exist.") from None
    except tomllib.TOMLDecodeError as exc:
        raise SdlcError(f"The file {path} is not valid TOML: {exc}.") from None
    except UnicodeDecodeError:
        raise SdlcError(f"The file {path} is not valid UTF-8 text.") from None


def deep_merge(base: dict, over: dict) -> dict:
    """Return a new dict: over merged into base. Tables merge. Other values replace."""
    result = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(repo: str | os.PathLike, defaults: dict | None = None) -> dict:
    """Return the effective config for a repo.

    Order, lowest first: CORE_DEFAULTS, then defaults (normally the stage
    [config_defaults] table), then sdlc/config.toml when it exists.
    """
    config = deep_merge(CORE_DEFAULTS, defaults or {})
    path = Path(repo) / CONFIG_REL
    if path.is_file():
        config = deep_merge(config, load_toml(path))
    return config


def flatten_keys(data: dict, prefix: str = "") -> list[str]:
    """Return the sorted dotted names of every non-table value in data."""
    keys = []
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            keys.extend(flatten_keys(value, name))
        else:
            keys.append(name)
    return sorted(keys)


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else _toml_string(key)


def _toml_string(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        if any(isinstance(item, dict) for item in value):
            raise SdlcError("dump_toml does not support arrays of tables.")
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise SdlcError(f"dump_toml does not support the type {type(value).__name__}.")


def dump_toml(data: dict) -> str:
    """Return TOML text for data. tomllib.loads(dump_toml(d)) == d.

    Supported values: str, int, float, bool, lists of these, and nested
    tables. Keys keep their insertion order.
    """
    lines: list[str] = []

    def emit(table: dict, path: list[str]) -> None:
        scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
        tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        if path and (scalars or not tables):
            if lines:
                lines.append("")
            lines.append("[" + ".".join(_toml_key(p) for p in path) + "]")
        for key, value in scalars:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        for key, value in tables:
            emit(value, path + [key])

    emit(data, [])
    return "\n".join(lines) + "\n" if lines else ""


# Stage definition ------------------------------------------------------------


def load_stage(stage_dir: str | os.PathLike) -> dict:
    """Read and check <stage_dir>/stage.toml. Return a normalized dict.

    Keys: id, version, agents_md (str or None), codeowners (list of str),
    install (list of dicts with src, dest, kind), config_defaults (dict).
    """
    stage_dir = Path(stage_dir)
    raw = load_toml(stage_dir / "stage.toml")
    where = f"{stage_dir / 'stage.toml'}"

    stage = raw.get("stage")
    if not isinstance(stage, dict):
        raise SdlcError(f"{where} has no [stage] table.")
    stage_id = stage.get("id")
    if not isinstance(stage_id, str) or not _STAGE_ID.match(stage_id):
        raise SdlcError(f"{where} needs [stage].id with lowercase letters, digits, and hyphens only.")
    version = stage.get("version")
    if not isinstance(version, str) or not version:
        raise SdlcError(f"{where} needs [stage].version as a string.")

    for name in ("agents_md", "codeowners"):
        if not isinstance(raw.get(name, {}), dict):
            raise SdlcError(f"{where} needs [{name}] as a table.")
    if not isinstance(raw.get("install", []), list):
        raise SdlcError(f"{where} needs [[install]] as an array of tables.")

    agents_md = raw.get("agents_md", {}).get("text")
    if agents_md is not None and not isinstance(agents_md, str):
        raise SdlcError(f"{where} needs [agents_md].text as a string.")

    paths = raw.get("codeowners", {}).get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise SdlcError(f"{where} needs [codeowners].paths as a list of strings.")

    entries = []
    for index, entry in enumerate(raw.get("install", []), start=1):
        if not isinstance(entry, dict):
            raise SdlcError(f"{where} has an [[install]] entry that is not a table.")
        src, dest, kind = entry.get("src"), entry.get("dest"), entry.get("kind")
        if not isinstance(src, str) or not isinstance(dest, str):
            raise SdlcError(f"{where} [[install]] entry {index} needs src and dest strings.")
        if kind not in INSTALL_KINDS:
            raise SdlcError(f"{where} [[install]] entry {index} needs kind \"managed\" or \"user\".")
        safe_join(stage_dir, src)
        safe_join(stage_dir, dest)
        entries.append({"src": src, "dest": dest, "kind": kind})

    defaults = raw.get("config_defaults", {})
    if not isinstance(defaults, dict):
        raise SdlcError(f"{where} needs [config_defaults] as a table.")

    return {
        "id": stage_id,
        "version": version,
        "agents_md": agents_md,
        "codeowners": list(paths),
        "install": entries,
        "config_defaults": defaults,
    }


# Managed sections --------------------------------------------------------------


def markers(stage: str, style: str) -> tuple[str, str]:
    """Return the begin and end marker lines for a stage. style is "md" or "hash"."""
    if style not in MARKER_STYLES:
        raise SdlcError(f"The marker style {style!r} is not known.")
    if not _STAGE_ID.match(stage):
        raise SdlcError(f"The stage id {stage!r} is not valid.")
    begin, end = MARKER_STYLES[style]
    return begin.format(stage=stage), end.format(stage=stage)


_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _marker_lines(lines: list[str], style: str) -> list[str | None]:
    """Return each line as a marker candidate, or None when it cannot be a marker.

    A marker starts at column 0. In markdown, a line inside a fenced code
    block is not a marker, so an example of the markers stays as it is.
    A fence that does not close before the end of the text is not a fence.
    Its opener line is then plain text and the scan continues after it.
    """
    found: list[str | None] = []
    fence = None
    fence_at = 0
    i = 0
    while i < len(lines):
        # An editor can save a UTF-8 BOM before the first line.
        bare = lines[i].lstrip(BOM).rstrip()
        if style == "md":
            opener = _MD_FENCE.match(bare)
            # CommonMark: a backtick fence has no backtick in its info string.
            if opener and opener.group(1)[0] == "`" and "`" in bare[opener.end():]:
                opener = None
            if fence is not None:
                if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                    if not bare.strip().lstrip(fence[0]):
                        fence = None
                found.append(None)
                i += 1
                if fence is not None and i == len(lines):
                    fence = None
                    del found[fence_at:]
                    found.append(lines[fence_at].lstrip(BOM).rstrip())
                    i = fence_at + 1
                continue
            if opener:
                fence = opener.group(1)
                fence_at = i
                found.append(None)
                i += 1
                if i == len(lines):
                    fence = None
                    found[-1] = bare
                continue
        found.append(bare)
        i += 1
    return found


def _find_section(text: str, stage: str, style: str):
    begin, end = markers(stage, style)
    lines = text.splitlines(keepends=True)
    candidates = _marker_lines(lines, style)
    starts = [i for i, line in enumerate(candidates) if line == begin]
    ends = [i for i, line in enumerate(candidates) if line == end]
    if not starts and not ends:
        return lines, None, None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise SdlcError(f"The markers for stage {stage} are broken. Fix them by hand.")
    return lines, starts[0], ends[0]


def get_section(text: str, stage: str, style: str) -> str | None:
    """Return the text between the markers, or None when there are no markers."""
    lines, start, end = _find_section(text, stage, style)
    if start is None:
        return None
    return "".join(lines[start + 1 : end])


def set_section(text: str, stage: str, body: str, style: str) -> str:
    """Return text with the stage section set to body.

    Text outside the markers stays the same. With no markers, the section
    goes at the end after one blank line. The body ends with one newline.
    New lines use the newline style of the begin marker line when it
    exists, else the style of the first line of text (CRLF or LF).
    The call is idempotent.
    """
    begin, end = markers(stage, style)
    lines, start, stop = _find_section(text, stage, style)
    first = lines[start] if start is not None else (lines[0] if lines else "")
    nl = "\r\n" if first.endswith("\r\n") else "\n"
    body = body.replace("\r\n", "\n").rstrip("\n").replace("\n", nl)
    block = begin + nl + (body + nl if body else "") + end + nl
    if start is None:
        if not text:
            return block
        prefix = text if text.endswith("\n") else text + nl
        if not prefix.endswith("\n" + nl):
            prefix += nl
        return prefix + block
    tail = "".join(lines[stop + 1 :])
    if not lines[stop].endswith("\n") and not tail:
        block = block[: -len(nl)]
    # Keep a BOM that sits before a begin marker on the first line.
    bom = BOM if start == 0 and lines[0].startswith(BOM) else ""
    return bom + "".join(lines[:start]) + block + tail


# Frontmatter -------------------------------------------------------------------


def _fm_scalar(raw: str):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_fm_scalar(part) for part in inner.split(",")] if inner else []
    return value


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown file into (frontmatter dict, body).

    The frontmatter sits between two "---" lines at the top. Grammar:
    "key: value", "key: [a, b]", "key: true|false", quoted strings, and a
    block list ("key:" then lines "  - item"). A file without frontmatter
    gives ({}, text). Raise SdlcError on a line that does not fit.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    data: dict = {}
    current = None
    for index in range(1, len(lines)):
        line = lines[index].rstrip("\r\n")
        if line.strip() == "---":
            return data, "".join(lines[index + 1 :])
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") or stripped == "-":
            if current is None:
                raise SdlcError(f"Frontmatter line {index + 1} has a list item with no key.")
            if data[current] == "":
                data[current] = []
            if not isinstance(data[current], list):
                raise SdlcError(f"Frontmatter key {current} mixes a value and a list.")
            data[current].append(_fm_scalar(stripped[1:]))
            continue
        if ":" not in line or line[0].isspace():
            raise SdlcError(f"Frontmatter line {index + 1} is not \"key: value\".")
        key, _, value = line.partition(":")
        key = key.strip()
        if not key:
            raise SdlcError(f"Frontmatter line {index + 1} has no key.")
        if key in data:
            raise SdlcError(f"Frontmatter key {key} appears two times.")
        data[key] = _fm_scalar(value) if value.strip() else ""
        current = key
    raise SdlcError("The frontmatter has no closing \"---\" line.")
