"""Leak scan and portability lint (FR-91, NFR-1, NFR-6). Owner: the scan module author.

The engine ships in every bundle, so this file must pass its own scans.
Pattern strings that look like a leak or a harness term are built from
parts. Do not write them as one literal.
"""

from __future__ import annotations

import codecs
import os
import re
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from . import common
from .common import SdlcError

BINARY_PROBE = 8192
ALLOWLIST_FILE = "leak-allowlist.txt"

_NAME = r"[A-Za-z0-9._-]+"

# Leak patterns (NFR-6). Order does not matter. Results sort at the end.
_LEAK_PATTERNS = (
    (
        "home-path",
        # Case-insensitive, with an optional JSON escape before each slash.
        # A "." may come first, so "../" paths match too.
        re.compile(r"(?<![\w~-])\\?/(?:" + "us" + "ers|" + "ho" + r"me)\\?/" + _NAME, re.IGNORECASE),
    ),
    (
        "home-path",
        re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]+" + "us" + "ers" + r"[\\/]+" + _NAME, re.IGNORECASE),
    ),
    (
        "home-path",
        # WSL drive mounts, for example "/mnt/c/" + "Users/<name>".
        re.compile(r"\\?/mnt\\?/[a-z]\\?/" + "us" + r"ers\\?/" + _NAME, re.IGNORECASE),
    ),
    (
        "home-path",
        # macOS volume forms of a home: the data volume, for example
        # "/System/Volumes/Data/" + "Users/<name>", and a mounted disk, for
        # example "/Volumes/<disk name>/" + "Users/<name>".
        re.compile(
            r"(?<![\w~-])\\?/(?:system\\?/volumes\\?/data|volumes\\?/[^/\\\n]+)\\?/(?:"
            + "us" + "ers|" + "ho" + r"me)\\?/" + _NAME,
            re.IGNORECASE,
        ),
    ),
    (
        "home-path",
        # Fedora Atomic and other systems that keep homes under /var.
        re.compile(r"\\?/var\\?/" + "ho" + r"me\\?/" + _NAME, re.IGNORECASE),
    ),
    (
        "email",
        re.compile(r"[A-Za-z0-9._%+-]+" + "@" + r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    ),
    (
        "token:github",
        re.compile(r"\b" + "gh" + r"[pousr]_[A-Za-z0-9]{36,}"),
    ),
    (
        "token:github",
        re.compile(r"\b" + "github" + "_pat_" + r"[A-Za-z0-9_]{22,}"),
    ),
    (
        "token:anthropic",
        re.compile(r"\b" + "sk" + "-ant-" + r"[A-Za-z0-9_-]{20,}"),
    ),
    (
        "token:secret-key",
        re.compile(r"\b" + "sk" + "-(?!ant-)" + r"[A-Za-z0-9_-]{20,}"),
    ),
    (
        "token:slack",
        re.compile(r"\b" + "xo" + r"x[abposr]-[A-Za-z0-9-]{10,}"),
    ),
    (
        "token:aws",
        re.compile(r"\b(?:" + "AK" + "IA|" + "AS" + r"IA)[0-9A-Z]{16}\b"),
    ),
    (
        "token:private-key",
        re.compile("-----" + "BEGIN" + r" (?:[A-Z0-9]+ )*" + "PRIVATE" + " KEY-----"),
    ),
)

# Portability denylist (NFR-1). The list comes from the requirements:
# the harness config folder, the subagent type key, the tool that starts a
# subagent, the ask-user tool, and the two harness print commands.
_PORTABLE_PATTERNS = (
    ("portable:harness-dir", re.compile(r"(?<![\w.])\." + "clau" + r"de(?![\w.-])", re.IGNORECASE)),
    ("portable:subagent-type", re.compile(r"\b" + "subagent" + r"_type\b", re.IGNORECASE)),
    ("portable:task-tool", re.compile(r"\b" + "Task" + r"\s+tool\b", re.IGNORECASE)),
    ("portable:ask-user", re.compile(r"\b" + "AskUser" + r"Question\b", re.IGNORECASE)),
    ("portable:codex-exec", re.compile(r"\b" + "codex" + r"\s+exec\b", re.IGNORECASE)),
    ("portable:claude-print", re.compile(r"\b" + "clau" + r"de\s+-p\b", re.IGNORECASE)),
)

HARNESS_NOTES_TITLE = "harness notes (optional)"
AGENT_RUNNER_SEGMENT = "agent_runner"

_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TOML_HEADER = re.compile(r"^\s*(\[\[?)\s*(.+?)\s*(\]\]?)\s*(?:#.*)?$")


@dataclass(frozen=True)
class Hit:
    """One scan finding. path is relative to the scanned folder."""

    path: str
    line: int
    pattern: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.pattern}"


# Helpers -----------------------------------------------------------------------


def _read_list(path: Path) -> list[str]:
    """Return the entries of a list file. Skip blank lines and "#" lines.

    A missing file gives an empty list. A UTF-8, UTF-16 or UTF-32 BOM is
    accepted. Any other text that is not UTF-8 is an error.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise SdlcError(f"The list file {path} cannot be read: {exc.strerror}.") from None
    text = _decode_bom(data)
    if text is None:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SdlcError(f"The list file {path} is not UTF-8 text. Save it as UTF-8.") from None
    entries = []
    for line in text.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            entries.append(entry)
    return entries


def _default_allowlist() -> list[str]:
    return _read_list(common.ENGINE_DIR / ALLOWLIST_FILE)


def denylist_path() -> str:
    """Return the denylist path as given: the env value, else the default."""
    return os.environ.get(common.LEAK_DENYLIST_ENV, "").strip() or common.DEFAULT_LEAK_DENYLIST


def _default_denylist() -> list[str]:
    return _read_list(Path(denylist_path()).expanduser())


# Magic bytes of compressed and archive formats. A leak inside one is not
# visible to a text scan, so leak_scan reports the file itself.
_ARCHIVE_MAGIC = (
    b"\x1f\x8b",  # gzip
    b"PK\x03\x04",  # zip, and formats built on zip
    b"PK\x05\x06",  # empty zip
    b"PK\x07\x08",  # spanned zip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"7z\xbc\xaf\x27\x1c",  # 7z
    b"\x28\xb5\x2f\xfd",  # zstd
    b"PACK\x00\x00\x00\x02",  # git pack, version 2
    b"PACK\x00\x00\x00\x03",  # git pack, version 3
)

# The second byte of a zlib header, for example a git loose object.
_ZLIB_LEVELS = (0x01, 0x5E, 0x9C, 0xDA)
_PDF_MAGIC = b"%PDF-"
_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)

# Longest BOM first: the UTF-32-LE BOM starts with the UTF-16-LE BOM.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _decode_bom(data: bytes) -> str | None:
    """Decode data that starts with a UTF-16 or UTF-32 BOM. Else return None."""
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return data.decode(codec, errors="replace")
    return None


def _decode(data: bytes, raw_binary: bool) -> list[str]:
    """Return the text variants of data to scan.

    With raw_binary=True, there are two variants for the whole file: the
    normal decode (BOM codec, else UTF-8), and the bytes with the NUL bytes
    removed, as Latin-1. Then UTF-16 text after ASCII text, ASCII after a
    BOM, and strings in a binary file are all scanned.
    With raw_binary=False, a file with a NUL byte in its first 8 KiB is
    binary and gives no variant, unless it starts with a BOM.
    """
    normal = _decode_bom(data)
    if raw_binary:
        if normal is None:
            normal = data.decode("utf-8", errors="replace")
        raw = data.replace(b"\x00", b"").decode("latin-1")
        return [normal] if raw == normal else [normal, raw]
    if b"\x00" in data[:BINARY_PROBE]:
        return [] if normal is None else [normal]
    return [data.decode("utf-8", errors="replace")]


def _inflate(data: bytes) -> bytes | None:
    """Return the zlib-decompressed data, or None when data is not zlib data."""
    try:
        return zlib.decompressobj().decompress(data)
    except zlib.error:
        return None


def is_archive(data: bytes) -> bool:
    """Return True when data is in a compressed or archive format.

    That is a known magic number, or a whole file of zlib data.
    """
    if data.startswith(_ARCHIVE_MAGIC):
        return True
    if len(data) >= 2 and data[0] == 0x78 and data[1] in _ZLIB_LEVELS:
        return _inflate(data) is not None
    return False


def _pdf_streams(data: bytes):
    """Yield (line number, lines) for each zlib stream in a PDF file."""
    if not data.startswith(_PDF_MAGIC):
        return
    for match in _PDF_STREAM.finditer(data):
        inflated = _inflate(match.group(1))
        if not inflated:
            continue
        number = data.count(b"\n", 0, match.start()) + 1
        for text in _decode(inflated, raw_binary=True):
            yield number, text.split("\n")


def _text_files(root: Path, raw_binary: bool = False):
    """Yield (relative POSIX path, list of lines, raw bytes) for each text variant under root.

    _decode gives the text variants. A file can give zero, one or two variants.
    """
    root = Path(root)
    if not root.is_dir():
        raise SdlcError(f"The folder {root} does not exist.")
    for rel in common.iter_files(root):
        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            raise SdlcError(f"The file {rel.as_posix()} cannot be read: {exc.strerror}.") from None
        for text in _decode(data, raw_binary):
            lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
            yield rel.as_posix(), lines, data


def _sorted(hits: set[Hit]) -> list[Hit]:
    return sorted(hits, key=lambda h: (h.path, h.line, h.pattern))


# Leak scan -----------------------------------------------------------------------


def _denylist_regex(parts: list[str]) -> re.Pattern:
    """Return the regex for one denylist entry, split into its words.

    The boundaries are letters only, so "jdoe2" and "owner_jdoe" match.
    After the entry, only a lowercase letter blocks a match, so the entry
    "jdoe" matches in the camelCase name "jdoeAdmin". The words of a
    multi-word entry may have no separator, or any run of spaces, "_", "."
    or "-" between them. So "Jane Doe" matches "jane_doe" and "JaneDoe".
    """
    body = r"[\s_.-]*".join(re.escape(part) for part in parts)
    return re.compile(r"(?<![A-Za-z])(?i:" + body + r")(?![a-z])")


def leak_scan(root: Path, allowlist: list[str] | None = None, denylist: list[str] | None = None) -> list[Hit]:
    """Return leak hits under root, sorted by path then line. See CONTRACT.md."""
    if allowlist is None:
        allowlist = _default_allowlist()
    if denylist is None:
        denylist = _default_denylist()
    allowed = [
        re.compile(re.escape(entry.strip()), re.IGNORECASE) for entry in allowlist if entry.strip()
    ]

    patterns = list(_LEAK_PATTERNS)
    for word in denylist:
        parts = word.split()
        if parts:
            patterns.append(("denylist", _denylist_regex(parts)))

    def check(path: str, number: int, line: str) -> None:
        # Scan the URL-decoded line too, so "%2F" cannot hide a slash.
        for variant in {line, unquote(line)}:
            # A match that lies fully inside an allowlist entry is not a hit.
            # So the denylist word "Jo" does not hit inside the allowed "JoTool".
            spans = [found.span() for regex in allowed for found in regex.finditer(variant)]
            for name, regex in patterns:
                if any(
                    not any(start <= match.start() and match.end() <= end for start, end in spans)
                    for match in regex.finditer(variant)
                ):
                    hits.add(Hit(path, number, name))

    hits: set[Hit] = set()
    for path, lines, data in _text_files(root, raw_binary=True):
        # Line 0 is the file path. A file or folder name can leak too.
        check(path, 0, path)
        if is_archive(data):
            # The scan cannot see inside a compressed file.
            hits.add(Hit(path, 0, "archive"))
        for number, line in enumerate(lines, start=1):
            check(path, number, line)
        # A PDF keeps most text in zlib streams. Scan each stream at its line.
        for number, stream_lines in _pdf_streams(data):
            for line in stream_lines:
                check(path, number, line)
    return _sorted(hits)


# Portability lint ----------------------------------------------------------------


def _markdown_exempt(lines: list[str]) -> list[bool]:
    """Mark the lines inside a "Harness notes (optional)" section.

    The section starts at its heading and ends before the next heading of
    the same or a higher level. Headings inside code fences do not count.
    """
    exempt = []
    section_level = None
    fence = None
    for line in lines:
        fence_match = _FENCE.match(line)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                if not line.strip().lstrip(fence[0]):
                    fence = None
            exempt.append(section_level is not None)
            continue
        if fence_match:
            fence = fence_match.group(1)
            exempt.append(section_level is not None)
            continue
        heading = _ATX_HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            title = re.sub(r"[ \t]+#+$", "", heading.group(2) or "").strip()
            if section_level is not None and level <= section_level:
                section_level = None
            if section_level is None and title.casefold() == HARNESS_NOTES_TITLE:
                section_level = level
        exempt.append(section_level is not None)
    return exempt


def _toml_segments(header: str) -> list[str]:
    """Split a TOML table header path into its segments."""
    segments = []
    current = []
    quote = None
    for ch in header:
        if quote:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch == ".":
            segments.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    segments.append("".join(current).strip())
    return segments


def _toml_exempt(lines: list[str]) -> list[bool]:
    """Mark the lines inside a TOML table whose header path has an agent_runner segment.

    Header-like lines inside multi-line strings do not count.
    """
    exempt = []
    in_table = False
    multiline = None
    for line in lines:
        if multiline is not None:
            if line.count(multiline) % 2 == 1:
                multiline = None
            exempt.append(in_table)
            continue
        header = _TOML_HEADER.match(line)
        if header and len(header.group(1)) == len(header.group(3)):
            in_table = AGENT_RUNNER_SEGMENT in _toml_segments(header.group(2))
        else:
            for delim in ('"""', "'''"):
                if line.count(delim) % 2 == 1:
                    multiline = delim
                    break
        exempt.append(in_table)
    return exempt


def lint_portable(root: Path) -> list[Hit]:
    """Return portability hits under root, sorted by path then line. See CONTRACT.md."""
    hits: set[Hit] = set()
    for path, lines, _data in _text_files(root):
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in (".md", ".markdown"):
            exempt = _markdown_exempt(lines)
        elif suffix == ".toml":
            exempt = _toml_exempt(lines)
        else:
            exempt = [False] * len(lines)
        for number, line in enumerate(lines, start=1):
            if exempt[number - 1]:
                continue
            for name, regex in _PORTABLE_PATTERNS:
                if regex.search(line):
                    hits.add(Hit(path, number, name))
    return _sorted(hits)
