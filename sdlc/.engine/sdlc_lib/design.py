"""spec.md, the policy check, and the Flags section (FR-30, FR-31, FR-34). Owner: the design stage.

The spec-writer role writes a spec draft. The engine checks the draft and
writes spec.md with the work item ID and the upstream links (FR-6). The
engine refuses a draft with no Flags section, or with a Policy check
section that does not name every configured policy file. A model never
writes the frontmatter or the source section.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import overlay
from .common import WORK_REL, SdlcError, parse_frontmatter, safe_join, safe_write_path, write_if_changed
from .verify import check_item_id

CONFIG_TABLE = "design"
POLICIES_KEY = "policies"
SPEC_NAME = "spec.md"
SPEC_TEMPLATE = "templates/spec.md"
INTENT_NAME = "intent.md"
FALLBACK = "inline problem statement"
FLAGS_SECTION = "Flags"
POLICY_SECTION = "Policy check"
NO_FLAGS_LINE = "None."
OPEN_RESOLUTION = "open"

_HEADING = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_FLAG_LINE = re.compile(r"^- (FLAG-[0-9]+)\b[:.]?\s*(.*)$")
_RESOLUTION_LINE = re.compile(r"^\s+(?:- )?Resolution:\s*(.*)$")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


@dataclass
class Flag:
    """One flag in the Flags section."""

    id: str
    text: str
    resolution: str = ""

    @property
    def open(self) -> bool:
        """A flag is open until a human writes a resolution."""
        return self.resolution.strip().rstrip(".").lower() in ("", OPEN_RESOLUTION)


def policy_list(config: dict) -> list[str]:
    """Return the [design].policies list. An absent key gives an empty list."""
    table = config.get(CONFIG_TABLE)
    table = table if isinstance(table, dict) else {}
    value = table.get(POLICIES_KEY, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SdlcError(f"The config key {CONFIG_TABLE}.{POLICIES_KEY} must be a list of repo-relative file paths.")
    return [item.strip() for item in value]


def policy_paths(repo: Path, config: dict) -> list[str]:
    """Return the configured policy files as repo-relative POSIX strings.

    Raise SdlcError when a file is missing, so that no policy is skipped.
    """
    found = []
    for rel in policy_list(config):
        path = safe_join(Path(repo), rel)
        if not path.is_file():
            raise SdlcError(
                f"The policy file {rel} in {CONFIG_TABLE}.{POLICIES_KEY} does not exist. "
                f"Add the file or remove it from sdlc/config.toml."
            )
        found.append(path.relative_to(Path(repo)).as_posix())
    return found


def _sections(body: str) -> list[tuple[str, list[str]]]:
    """Return (heading, lines) for each level-2 heading outside a code fence."""
    sections: list[tuple[str, list[str]]] = []
    fence = None
    for line in body.splitlines():
        match = _FENCE.match(line)
        if match:
            mark = match.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
        if fence is None and not match:
            heading = _HEADING.match(line)
            if heading:
                sections.append((heading.group(1), []))
                continue
        if sections:
            sections[-1][1].append(line)
    return sections


def _one_section(body: str, name: str) -> list[str]:
    found = [lines for heading, lines in _sections(body) if heading.lower() == name.lower()]
    if not found:
        raise SdlcError(f"The spec has no '## {name}' section. The spec-writer must always write it.")
    if len(found) > 1:
        raise SdlcError(f"The spec has more than one '## {name}' section.")
    return found[0]


def draft_body(draft: str) -> str:
    """Return the draft from its first level-2 heading on.

    A harness can print text before the spec. The engine drops it.
    """
    lines = draft.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if _HEADING.match(line.rstrip("\r\n")):
            return "".join(lines[index:]).rstrip() + "\n"
    raise SdlcError("The spec draft has no '## ' section. Run the spec-writer role again.")


def parse_flags(body: str) -> list[Flag]:
    """Return the flags of the Flags section (FR-31).

    The section holds the line "None." or one or more flag lines
    "- FLAG-<n>: <text>". Indented lines below a flag continue it. An
    indented "Resolution: <text>" line holds the decision of a human.
    Any other text raises SdlcError, because free text can hide a guess.
    """
    flags: list[Flag] = []
    has_none = False
    for line in _one_section(body, FLAGS_SECTION):
        text = line.rstrip()
        if not text.strip():
            continue
        flag = _FLAG_LINE.match(text)
        if flag:
            if any(item.id == flag.group(1) for item in flags):
                raise SdlcError(f"The Flags section has {flag.group(1)} more than one time.")
            flags.append(Flag(flag.group(1), flag.group(2).strip()))
            continue
        if text.strip() == NO_FLAGS_LINE and not text[0].isspace():
            has_none = True
            continue
        if text[0].isspace() and flags:
            resolution = _RESOLUTION_LINE.match(text)
            if resolution:
                flags[-1].resolution = resolution.group(1).strip()
            else:
                flags[-1].text = (flags[-1].text + " " + text.strip()).strip()
            continue
        raise SdlcError(
            f"The Flags section has a line that is not a flag: {text.strip()!r}. "
            f"Write each flag as '- FLAG-<n>: <text>', or write '{NO_FLAGS_LINE}' when there is no flag."
        )
    if has_none and flags:
        raise SdlcError(f"The Flags section has flags and the line '{NO_FLAGS_LINE}'. Remove one of them.")
    if not has_none and not flags:
        raise SdlcError(f"The Flags section is empty. Write '{NO_FLAGS_LINE}' when there is no flag.")
    return flags


def check_policy_section(body: str, policies: list[str]) -> None:
    """Raise SdlcError when the Policy check section is absent or skips a policy file (FR-30)."""
    text = "\n".join(_one_section(body, POLICY_SECTION))
    missing = [rel for rel in policies if rel not in text]
    if missing:
        raise SdlcError(
            "The Policy check section does not name these policy files: " + ", ".join(missing)
            + ". The spec-writer must check every configured policy."
        )


def check_flag_history(old: list[Flag], new: list[Flag]) -> None:
    """Raise SdlcError when a draft drops a flag or writes a resolution (FR-31, CON-4).

    A new draft keeps every flag of the earlier spec.md. A resolution in the
    draft must be the resolution that a human wrote in the earlier spec.md,
    word for word. A draft never resolves a flag by itself.
    """
    new_ids = {flag.id for flag in new}
    dropped = [flag.id for flag in old if flag.id not in new_ids]
    if dropped:
        raise SdlcError(
            "The draft drops these flags of the earlier spec.md: " + ", ".join(dropped)
            + ". A new draft keeps every flag."
        )
    human = {flag.id: flag.resolution.strip() for flag in old if not flag.open}
    for flag in new:
        if flag.open:
            continue
        if human.get(flag.id) != flag.resolution.strip():
            raise SdlcError(
                f"The draft writes a resolution for {flag.id} that no human wrote in spec.md. "
                f"Only a human resolves a flag. The draft must say 'Resolution: {OPEN_RESOLUTION}' or copy the human resolution."
            )


def _render(template: str, values: dict[str, str]) -> str:
    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise SdlcError(f"The spec template has the unknown placeholder {{{{{name}}}}}.")
        return values[name]

    return _PLACEHOLDER.sub(fill, template)


def _upstream(item_dir: Path, links: list[str] | None, problem: str | None) -> tuple[str, str]:
    """Return (upstream frontmatter lines, source section text) (FR-3, FR-6, FR-34)."""
    has_intent = (item_dir / INTENT_NAME).is_file()
    if problem is not None:
        if has_intent or links:
            raise SdlcError(
                f"The work item has an upstream artifact. Give a problem statement only when there is no {INTENT_NAME}."
            )
        lines = problem.strip().splitlines()
        if not lines:
            raise SdlcError("The problem statement is empty.")
        quoted = "\n".join(("> " + line).rstrip() for line in lines)
        source = (
            f"There is no {INTENT_NAME}. The user gave this problem statement inline. "
            f"The spec uses it as the upstream input.\n\n{quoted}"
        )
        return f"upstream: {json.dumps('none, used ' + FALLBACK)}", source
    found = list(links or [])
    if not found and has_intent:
        found = [INTENT_NAME]
    if not found:
        raise SdlcError(
            f"The work item has no {INTENT_NAME}. Give the problem statement with --problem or --problem-file."
        )
    for link in found:
        if not link.strip() or "\n" in link:
            raise SdlcError("An upstream link must be one line.")
    front = "upstream:\n" + "\n".join(f"  - {json.dumps(link)}" for link in found)
    source = "Upstream artifacts:\n\n" + "\n".join(f"- [{link}]({link})" for link in found)
    return front, source


def _policies_front(policies: list[str]) -> str:
    if not policies:
        return "policies: none"
    return "policies:\n" + "\n".join(f"  - {json.dumps(rel)}" for rel in policies)


def write_spec(item_id: str, repo: Path, stage_dir: Path, draft: str, config: dict,
               problem: str | None = None, links: list[str] | None = None) -> tuple[Path, list[Flag]]:
    """Check the draft and write sdlc/work/<id>/spec.md. Return (path, flags).

    All checks run before the write. A draft that fails a check writes nothing.
    When spec.md exists, the draft must keep its flags and human resolutions.
    """
    check_item_id(item_id)
    repo = Path(repo)
    dest = safe_write_path(repo, f"{WORK_REL}/{item_id}/{SPEC_NAME}")
    policies = policy_paths(repo, config)
    upstream, source = _upstream(dest.parent, links, problem)
    body = draft_body(draft)
    flags = parse_flags(body)
    check_policy_section(body, policies)
    old_flags = []
    if dest.is_file():
        _front, old_body = parse_frontmatter(dest.read_text(encoding="utf-8"))
        old_flags = parse_flags(old_body)
    check_flag_history(old_flags, flags)
    template = overlay.resolve(SPEC_TEMPLATE, stage_dir, repo).read_text(encoding="utf-8")
    text = _render(template, {
        "work_item": item_id,
        "upstream": upstream,
        "policies": _policies_front(policies),
        "source": source,
        "body": body.rstrip("\n"),
    })
    write_if_changed(dest, text)
    return dest, flags


def check_spec(item_id: str, repo: Path, config: dict) -> list[Flag]:
    """Check an existing spec.md. Return its flags. Raise SdlcError when it is not valid."""
    check_item_id(item_id)
    path = safe_join(Path(repo), f"{WORK_REL}/{item_id}/{SPEC_NAME}")
    if not path.is_file():
        raise SdlcError(f"The work item {item_id} has no {SPEC_NAME}. Run the design stage first.")
    front, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if front.get("work_item") != item_id:
        raise SdlcError(f"The work_item value in {SPEC_NAME} is not {item_id}.")
    if "upstream" not in front:
        raise SdlcError(f"{SPEC_NAME} has no upstream value.")
    flags = parse_flags(body)
    check_policy_section(body, policy_paths(Path(repo), config))
    return flags
