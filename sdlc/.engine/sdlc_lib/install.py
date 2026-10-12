"""Stage setup (FR-1, FR-5, NFR-3). Owner: the install module author.

Ownership rule: setup rewrites managed files every time. It creates
user-owned files only when they are absent. It never writes overlay files.
It reports new config keys and never edits an existing config.toml.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import common
from .common import SdlcError

# The folder that holds sdlc.py. Setup copies it to sdlc/.engine/.
CORE_DIR = common.ENGINE_DIR

AGENTS_REL = "AGENTS.md"
CODEOWNERS_REL = ".github/CODEOWNERS"
# GitHub reads the first CODEOWNERS it finds in this order.
CODEOWNERS_LOOKUP = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
OWNER_KEY = "codeowners.owner"
OWNER_PLACEHOLDER = "OWNER"

# Install entries must not write to these. Setup owns them in other steps,
# or the user owns them (the overlay).
_RESERVED_PREFIXES = common.OVERLAY_PREFIXES + (common.ENGINE_REL,)
_RESERVED_FILES = (AGENTS_REL, common.CONFIG_REL) + CODEOWNERS_LOOKUP

_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9._-]+)?$")


@dataclass
class SetupReport:
    """Result of one setup call. Paths are repo-relative POSIX strings."""

    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    new_config_keys: list[str] = field(default_factory=list)
    config_created: bool = False
    pr_url: str | None = None   # the setup PR, when setup ran with open_pr=True


def setup(stage_dir: Path, repo: Path, open_pr: bool = False) -> SetupReport:
    """Install the stage at stage_dir into repo. See CONTRACT.md.

    open_pr=True runs setup on a new branch, pushes it as the bot, and
    opens the setup PR as the bot (see setup_pr.open_setup_pr).

    All checks run before the first write, so a bad stage or a bad config
    leaves the repo as it was. Setup refuses to write through a symlink in
    the repo, so it never writes or deletes a file outside the repo. The
    one exception is an AGENTS.md symlink to a file in the repo. Setup then
    writes the target file.
    """
    if open_pr:
        from . import setup_pr

        return setup_pr.open_setup_pr(stage_dir, repo)
    stage_dir = Path(stage_dir)
    repo = Path(repo)
    if not repo.is_dir():
        raise SdlcError(f"The repo folder {repo} does not exist.")
    stage = common.load_stage(stage_dir)

    # Checks. No writes in this part.
    installs = _check_installs(stage, stage_dir, repo)
    config_path = common.safe_write_path(repo, common.CONFIG_REL)
    if config_path.exists() and not config_path.is_file():
        raise SdlcError(f"{common.CONFIG_REL} is not a file.")
    existing_config = common.load_toml(config_path) if config_path.is_file() else None
    # dump_toml can fail on some TOML values, so make the text before any write.
    config_text = common.dump_toml(stage["config_defaults"]) if existing_config is None else None
    engine_root = _check_engine(repo)
    agents_text = None
    if stage["agents_md"] is not None:
        agents_rel = _agents_rel(repo)
        common.safe_write_path(repo, agents_rel)
        agents_text = _new_section_text(repo, agents_rel, stage["id"], stage["agents_md"], "md")
    codeowners = None
    owner_missing = False
    if stage["codeowners"]:
        effective = common.deep_merge(stage["config_defaults"], existing_config or {})
        owner, owner_missing = _owner(effective)
        body = "".join(f"{_check_codeowners_path(p)} @{owner}\n" for p in stage["codeowners"])
        rel = _codeowners_rel(repo)
        if rel != CODEOWNERS_REL:
            # The gate owner must also own a CODEOWNERS file outside .github/.
            body += f"/{rel} @{owner}\n"
        common.safe_write_path(repo, rel)
        codeowners = (rel, _new_section_text(repo, rel, stage["id"], body, "hash"))

    # Writes.
    report = SetupReport()
    for entry, src, dest in installs:
        if entry["kind"] == "user" and (dest.exists() or dest.is_symlink()):
            report.skipped.append(entry["dest"])
            continue
        if common.write_if_changed(dest, src.read_bytes()):
            report.written.append(entry["dest"])
    if agents_text is not None:
        if common.write_if_changed(common.safe_join(repo, agents_rel), agents_text):
            report.written.append(AGENTS_REL)
    if codeowners is not None:
        rel, text = codeowners
        if common.write_if_changed(common.safe_join(repo, rel), text):
            report.written.append(rel)
    written, removed = _sync_engine(engine_root)
    report.written.extend(written)
    report.removed.extend(removed)

    defaults = stage["config_defaults"]
    if existing_config is None:
        common.write_if_changed(config_path, config_text)
        report.config_created = True
        missing = set()
    else:
        missing = set(common.flatten_keys(defaults)) - set(common.flatten_keys(existing_config))
    if owner_missing:
        missing.add(OWNER_KEY)
    report.new_config_keys = sorted(missing)
    return report


def _fold(rel: str) -> str:
    # Compare paths case-insensitively, so that a case-insensitive disk
    # cannot let "SDLC/Agents/x" reach the overlay.
    return PurePosixPath(rel).as_posix().casefold()


def _is_reserved(rel: str) -> bool:
    folded = _fold(rel)
    for prefix in _RESERVED_PREFIXES:
        prefix = prefix.casefold()
        if folded == prefix or folded.startswith(prefix + "/"):
            return True
    return folded in {f.casefold() for f in _RESERVED_FILES}


def _check_installs(stage: dict, stage_dir: Path, repo: Path) -> list[tuple[dict, Path, Path]]:
    seen = set()
    checked = []
    for entry in stage["install"]:
        dest_rel = entry["dest"]
        if _is_reserved(dest_rel):
            raise SdlcError(
                f"The install destination {dest_rel} is a path that setup does not let a stage write."
            )
        if _fold(dest_rel) in seen:
            raise SdlcError(f"The install destination {dest_rel} appears two times.")
        seen.add(_fold(dest_rel))
        src = common.safe_join(stage_dir, entry["src"])
        if not src.is_file():
            raise SdlcError(f"The install source {entry['src']} is not a file in the stage folder.")
        # A user file that exists stays as it is, even as a symlink. Its
        # parent folders must still not be symlinks.
        dest = common.safe_write_path(repo, dest_rel, allow_leaf_link=entry["kind"] == "user")
        if entry["kind"] == "managed" and dest.exists() and not dest.is_file():
            raise SdlcError(f"The install destination {dest_rel} is not a file.")
        checked.append((entry, src, dest))
    return checked


def _agents_rel(repo: Path) -> str:
    """Return the repo-relative path that setup writes for AGENTS.md.

    Many repos keep AGENTS.md as a symlink to CLAUDE.md. Setup then writes
    the link target. The target must be in the repo, outside .git, and not
    a path that setup or the user owns in another way.
    """
    link = repo / AGENTS_REL
    if not link.is_symlink():
        return AGENTS_REL
    target = Path(os.path.realpath(link))
    try:
        rel = target.relative_to(repo.resolve()).as_posix()
    except ValueError:
        raise SdlcError(
            f"{AGENTS_REL} is a symlink to a file outside the repo. Setup does not write through it."
        ) from None
    if rel == "." or _is_reserved(rel) or PurePosixPath(rel).parts[0] == ".git":
        raise SdlcError(f"{AGENTS_REL} is a symlink to {rel}. Setup does not write that path.")
    return rel


def _read_text(path: Path, rel: str) -> str:
    # Read bytes and decode, so that line endings stay as they are.
    if not path.exists():
        return ""
    if not path.is_file():
        raise SdlcError(f"{rel} is not a file.")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise SdlcError(f"{rel} is not valid UTF-8 text.") from None


def _new_section_text(repo: Path, rel: str, stage_id: str, body: str, style: str) -> str:
    text = _read_text(common.safe_join(repo, rel), rel)
    try:
        return common.set_section(text, stage_id, body, style)
    except SdlcError as exc:
        raise SdlcError(f"{rel}: {exc.message}") from None


def _codeowners_rel(repo: Path) -> str:
    # Use the CODEOWNERS file that GitHub reads now. A new .github/CODEOWNERS
    # next to a root CODEOWNERS would hide the user's rules.
    for rel in CODEOWNERS_LOOKUP:
        if common.safe_join(repo, rel).is_file():
            return rel
    return CODEOWNERS_REL


def _owner(config: dict) -> tuple[str, bool]:
    table = config.get("codeowners")
    value = table.get("owner") if isinstance(table, dict) else None
    if value is None:
        return OWNER_PLACEHOLDER, True
    if not isinstance(value, str):
        raise SdlcError(f"The config key {OWNER_KEY} must be a string.")
    owner = value.strip().removeprefix("@")
    if not _OWNER.match(owner):
        raise SdlcError(
            f"The config key {OWNER_KEY} must be a GitHub user or team, for example \"octocat\" or \"org/team\"."
        )
    return owner, False


def _check_codeowners_path(path: str) -> str:
    if not path.strip() or any(ch.isspace() for ch in path) or path.startswith("#"):
        raise SdlcError(f"The CODEOWNERS path {path!r} is not valid. Use a path with no spaces.")
    return path


def _check_engine(repo: Path) -> Path:
    """Check sdlc/.engine/ before any write. Return its path.

    The folder and its parents must not be symlinks, and nothing inside it
    may be a symlink. Then the sync cannot write or delete outside the repo.
    """
    dest_root = common.safe_write_path(repo, common.ENGINE_REL)
    if dest_root.exists() and not dest_root.is_dir():
        raise SdlcError(f"{common.ENGINE_REL} is not a folder.")
    if dest_root.is_dir():
        for folder, dirs, files in os.walk(dest_root):
            for name in dirs + files:
                path = Path(folder) / name
                if path.is_symlink():
                    rel = path.relative_to(repo).as_posix()
                    raise SdlcError(f"{rel} is a symlink. The engine does not write through a symlink.")
    return dest_root


def _sync_engine(dest_root: Path) -> tuple[list[str], list[str]]:
    """Make sdlc/.engine/ match CORE_DIR. Return (written, removed) paths.

    _check_engine must pass first. The stale pass runs before the copy. On a
    case-insensitive disk, "COMMON.py" and "common.py" are one file, so a
    stale pass after the copy would delete the file that the copy wrote.
    """
    written, removed = [], []
    wanted = common.iter_files(CORE_DIR)
    wanted_set = {p.as_posix() for p in wanted}
    if dest_root.is_dir():
        for rel in common.iter_files(dest_root, followlinks=False):
            if rel.as_posix() not in wanted_set:
                (dest_root / rel).unlink()
                removed.append(f"{common.ENGINE_REL}/{rel.as_posix()}")
        _remove_empty_dirs(dest_root)
    for rel in wanted:
        if common.write_if_changed(dest_root / rel, (CORE_DIR / rel).read_bytes()):
            written.append(f"{common.ENGINE_REL}/{rel.as_posix()}")
    return sorted(written), sorted(removed)


def _remove_empty_dirs(root: Path) -> None:
    for folder, _dirs, _files in sorted(os.walk(root), key=lambda t: len(t[0]), reverse=True):
        path = Path(folder)
        if path != root and not any(path.iterdir()):
            path.rmdir()
