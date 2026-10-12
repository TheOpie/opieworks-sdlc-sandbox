"""Overlay resolution (FR-4). Owner: the overlay module author.

The overlay is <repo>/sdlc/templates/ and <repo>/sdlc/agents/. A relative
path such as "templates/x.md" resolves to the overlay copy first, then to the
bundle default in the stage folder.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .common import OVERLAY_DIR, OVERLAY_PREFIXES, SdlcError, safe_join, safe_write_path

# The first folder of a relpath that the overlay holds, for example "templates".
_OVERLAY_FOLDERS = tuple(PurePosixPath(prefix).relative_to(OVERLAY_DIR).as_posix() for prefix in OVERLAY_PREFIXES)


def _paths(relpath: str, stage_dir: Path, repo: Path) -> tuple[Path, Path]:
    """Return the overlay path and the default path.

    Reject an unsafe relpath, and a relpath outside the overlay folders.
    """
    overlay = safe_join(Path(repo) / OVERLAY_DIR, relpath)
    default = safe_join(Path(stage_dir), relpath)
    parts = PurePosixPath(relpath).parts
    if not parts or parts[0].casefold() not in _OVERLAY_FOLDERS:
        folders = ", ".join(f"{name}/" for name in _OVERLAY_FOLDERS)
        raise SdlcError(f"The path {relpath!r} is not in the overlay. Use a path under {folders}.")
    return overlay, default


def resolve(relpath: str, stage_dir: Path, repo: Path) -> Path:
    """Return <repo>/sdlc/<relpath> when it is a file, else <stage_dir>/<relpath>.

    Raise SdlcError when relpath is unsafe or when neither file exists.
    """
    overlay, default = _paths(relpath, stage_dir, repo)
    if overlay.is_file():
        return overlay
    if default.is_file():
        return default
    raise SdlcError(f"No overlay file and no bundle default exist for {relpath!r}.")


def eject(relpath: str, stage_dir: Path, repo: Path) -> Path:
    """Copy <stage_dir>/<relpath> to <repo>/sdlc/<relpath>. Return the new path.

    Raise SdlcError when relpath is unsafe, when the default does not
    exist, or when the overlay file exists. Never overwrite an overlay file.
    """
    overlay, default = _paths(relpath, stage_dir, repo)
    if not default.is_file():
        raise SdlcError(f"The bundle default {relpath!r} does not exist.")
    # A symlinked folder in the overlay path could send the copy outside the
    # repo. The lexists check below covers the file itself.
    safe_write_path(Path(repo), f"{OVERLAY_DIR}/{relpath}", allow_leaf_link=True)
    # lexists also catches a broken symlink, so eject never writes through one.
    if os.path.lexists(overlay):
        raise SdlcError(f"The overlay file {OVERLAY_DIR}/{relpath} exists. Eject does not overwrite it.")
    data = default.read_bytes()
    try:
        overlay.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise SdlcError(f"The overlay folder for {relpath!r} cannot be made. A file is in the way.") from None
    # Mode "xb" fails when the file appears between the check and the write.
    try:
        with open(overlay, "xb") as handle:
            handle.write(data)
    except FileExistsError:
        raise SdlcError(f"The overlay file {OVERLAY_DIR}/{relpath} exists. Eject does not overwrite it.") from None
    return overlay
