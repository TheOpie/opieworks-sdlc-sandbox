"""Work items (FR-6). Owner: the workitem module author."""

from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .common import (
    WORK_REL,
    SdlcError,
    dump_toml,
    parse_frontmatter,
    safe_join,
    safe_write_path,
    write_if_changed,
)

LANES = ("fast", "full")

SLUG_MAX = 48
ITEM_FILE = "item.toml"
NO_UPSTREAM_PREFIX = "none, used "

_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_ITEM_ID = re.compile(r"^[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Artifact:
    """One artifact file in a work item folder."""

    name: str
    work_item: str | None
    upstream: list[str] = field(default_factory=list)


def slugify(title: str) -> str:
    """Return a lowercase ASCII slug: letters, digits, single hyphens, max 48 chars."""
    if not isinstance(title, str):
        raise SdlcError("The title must be text.")
    slug = _NOT_SLUG.sub("-", title.lower()).strip("-")
    slug = slug[:SLUG_MAX].strip("-")
    if not slug:
        raise SdlcError(f"The title {title!r} has no ASCII letters or digits. Use a title with at least one.")
    return slug


def _work_dir(repo: Path, item_id: str) -> Path:
    return safe_join(Path(repo), f"{WORK_REL}/{item_id}")


def new_item(title: str, lane: str, repo: Path, today: datetime.date | None = None) -> str:
    """Make <repo>/sdlc/work/<YYYYMMDD-slug>/ with item.toml. Return the ID."""
    if lane not in LANES:
        raise SdlcError(f"The lane {lane!r} is not known. Use one of: {', '.join(LANES)}.")
    slug = slugify(title)
    day = today or datetime.date.today()
    item_id = f"{day:%Y%m%d}-{slug}"
    # safe_write_path refuses a symlink anywhere on the path, so the write
    # stays inside the repo. lexists also finds a dangling symlink.
    dest = safe_write_path(Path(repo), f"{WORK_REL}/{item_id}/{ITEM_FILE}")
    if os.path.lexists(dest.parent):
        raise SdlcError(f"The work item {item_id} exists. Use a different title.")
    data = {"id": item_id, "title": title.strip(), "lane": lane, "created": day.isoformat()}
    write_if_changed(dest, dump_toml(data))
    return item_id


def _upstream_list(value, name: str) -> list[str]:
    """Normalize the upstream frontmatter value to a list of links."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        if value == "none" or value.startswith(NO_UPSTREAM_PREFIX):
            return []
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item for item in value if item]
    raise SdlcError(f"The artifact {name} has an upstream value that is not a link list.")


def show_item(item_id: str, repo: Path) -> list[Artifact]:
    """Return the artifacts of a work item, sorted by name."""
    if not isinstance(item_id, str) or not _ITEM_ID.match(item_id):
        raise SdlcError(f"The work item ID {item_id!r} is not valid. The format is YYYYMMDD-<slug>.")
    folder = _work_dir(repo, item_id)
    if not folder.is_dir():
        raise SdlcError(f"The work item {item_id} does not exist.")
    artifacts = []
    for path in sorted(folder.glob("*.md"), key=lambda p: p.name):
        if not path.is_file():
            continue
        try:
            meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except SdlcError as exc:
            raise SdlcError(f"The artifact {path.name} has bad frontmatter. {exc.message}") from None
        except UnicodeDecodeError:
            raise SdlcError(f"The artifact {path.name} is not valid UTF-8 text.") from None
        work_item = meta.get("work_item")
        if work_item is not None and not isinstance(work_item, str):
            raise SdlcError(f"The artifact {path.name} has a work_item value that is not text.")
        artifacts.append(
            Artifact(
                name=path.name,
                work_item=work_item or None,
                upstream=_upstream_list(meta.get("upstream"), path.name),
            )
        )
    return artifacts
