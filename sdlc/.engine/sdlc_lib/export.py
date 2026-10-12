"""Bundle export (FR-90, FR-91, SCN-8). Owner: the export module author."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from . import scan
from .common import EXCLUDED_DIRS, LEAK_DENYLIST_ENV, SdlcError, iter_files, parse_frontmatter, write_if_changed

CORE_LINK = "core"
PORTING_TEMPLATE = "templates/PORTING.md"
PORTING_NAME = "PORTING.md"
SKILL_PLACEHOLDER = "{{skill}}"
# A scaffold skill starts its SKILL.md description with this text.
SCAFFOLD_MARK = "SCAFFOLD ONLY"
# Template lines "{{if <kind>}}" and "{{end}}" keep or drop the lines between them.
_IF = re.compile(r"^\{\{if ([a-z]+)\}\}$")
_END = "{{end}}"


def _porting_kinds(skill_dir: Path) -> dict[str, bool]:
    """Return the template conditions for a skill.

    A skill with a stage.toml is a stage. A skill without one is the hub.
    A built stage is a stage that is not a scaffold.
    """
    stage = (skill_dir / "stage.toml").is_file()
    scaffold = False
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file():
        try:
            meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except (SdlcError, UnicodeDecodeError):
            meta = {}
        description = meta.get("description")
        scaffold = isinstance(description, str) and description.startswith(SCAFFOLD_MARK)
    return {"hub": not stage, "stage": stage, "scaffold": scaffold, "built": stage and not scaffold}


def render_porting(template: str, skill_dir: Path) -> str:
    """Return PORTING.md text for skill_dir from the template text.

    Lines between "{{if <kind>}}" and "{{end}}" stay only when the kind
    holds. The kinds are hub, stage, scaffold, and built. Blocks can nest.
    Raise SdlcError on an unknown kind or an unbalanced block.
    """
    skill_dir = Path(skill_dir)
    kinds = _porting_kinds(skill_dir)
    kept = []
    stack: list[bool] = []
    for line in template.splitlines(keepends=True):
        bare = line.strip()
        opener = _IF.match(bare)
        if opener:
            if opener.group(1) not in kinds:
                raise SdlcError(f"The PORTING template has the unknown condition {opener.group(1)!r}.")
            stack.append(kinds[opener.group(1)])
        elif bare == _END:
            if not stack:
                raise SdlcError(f"The PORTING template has a {_END} line with no matching if line.")
            stack.pop()
        elif all(stack):
            kept.append(line)
    if stack:
        raise SdlcError(f"The PORTING template has an if line with no {_END} line.")
    return "".join(kept).replace(SKILL_PLACEHOLDER, skill_dir.resolve().name)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _check_links(skill_dir: Path) -> None:
    """Refuse unsafe symlinks before the copy.

    The top-level "core" symlink may point outside the skill tree. Links
    inside the core tree must stay inside the core or the skill tree.
    Every other link must stay inside the skill tree. A link must not be
    broken and must not point at a folder above itself.
    """
    skill_root = skill_dir.resolve()
    core = skill_dir / CORE_LINK
    core_root = core.resolve() if core.is_symlink() else None

    def walk(folder: Path, rel: str, ancestors: frozenset) -> None:
        for entry in sorted(os.scandir(folder), key=lambda e: e.name):
            if entry.is_dir(follow_symlinks=False) and entry.name in EXCLUDED_DIRS:
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            path = Path(entry.path)
            if entry.is_symlink():
                target = path.resolve()
                if not target.exists():
                    raise SdlcError(f"The symlink {child_rel} is broken. Fix or remove it before the export.")
                if child_rel == CORE_LINK:
                    if not target.is_dir():
                        raise SdlcError("The core symlink must point to a folder.")
                elif core_root is not None and child_rel.startswith(CORE_LINK + "/"):
                    if not (_inside(target, core_root) or _inside(target, skill_root)):
                        raise SdlcError(f"The symlink {child_rel} points outside the core and the skill. The export refuses it.")
                elif not _inside(target, skill_root):
                    raise SdlcError(f"The symlink {child_rel} points outside the skill. The export refuses it.")
            if entry.is_dir(follow_symlinks=True):
                real = path.resolve()
                if real in ancestors:
                    raise SdlcError(f"The symlink {child_rel} makes a loop. The export refuses it.")
                walk(path, child_rel, ancestors | {real})

    walk(skill_dir, "", frozenset({skill_root}))


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def export(skill_dir: Path, out: Path, require_denylist: bool = True) -> Path:
    """Write a standalone bundle of skill_dir at out. Return out.

    Raise SdlcError and leave nothing at out on any scan hit. With
    require_denylist=True, raise SdlcError when the leak denylist is missing
    or empty, because only the denylist finds personal names and account IDs.
    """
    skill_dir = Path(skill_dir)
    out = Path(out)
    if os.path.lexists(out):
        raise SdlcError(f"The output folder {out} exists. Choose a new folder.")
    if not skill_dir.is_dir():
        raise SdlcError(f"The skill folder {skill_dir} does not exist.")
    if not out.parent.is_dir():
        raise SdlcError(f"The parent folder of {out} does not exist. Make it first.")

    skill_root = skill_dir.resolve()
    out_real = out.parent.resolve() / out.name
    core = skill_dir / CORE_LINK
    roots = [skill_root] + ([core.resolve()] if core.exists() else [])
    if any(_inside(out_real, root) for root in roots):
        raise SdlcError("The output folder must be outside the skill folder and its core.")

    _check_links(skill_dir)

    template = core / PORTING_TEMPLATE
    if not template.is_file():
        raise SdlcError(f"The skill has no {CORE_LINK}/{PORTING_TEMPLATE}. Link the engine as {CORE_LINK} first.")

    if require_denylist and not scan._default_denylist():
        raise SdlcError(
            f"The leak denylist {scan.denylist_path()} is missing or empty. "
            f"Without it, the export cannot find personal names or account IDs. "
            f"Write one word on each line in that file, or name another file in {LEAK_DENYLIST_ENV}. "
            f"To export with no denylist, use --no-denylist."
        )

    staged = Path(tempfile.mkdtemp(prefix=f".{out.name}.export-", dir=out.parent))
    try:
        for rel in iter_files(skill_dir):
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_dir / rel, dest)

        text = render_porting(template.read_text(encoding="utf-8"), skill_dir)
        write_if_changed(staged / PORTING_NAME, text)

        hits = list(scan.leak_scan(staged)) + list(scan.lint_portable(staged))
        if hits:
            hits.sort(key=lambda h: (h.path, h.line, h.pattern))
            raise SdlcError(
                f"The export found {_plural(len(hits), 'hit')}. The first hit is {hits[0].format()}. "
                f"Nothing was written to {out}."
            )

        if os.path.lexists(out):
            raise SdlcError(f"The output folder {out} exists. Choose a new folder.")
        # mkdtemp makes a 0700 folder. Give the bundle the normal folder mode.
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(staged, 0o777 & ~mask)
        os.rename(staged, out)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return out
