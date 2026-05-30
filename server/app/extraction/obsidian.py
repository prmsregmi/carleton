"""Obsidian vault I/O — the only place that knows notes are Markdown files.

A slim writer salvaged from the old knowledge-graph vault: structured fields
render as YAML frontmatter (a flat map of scalars and string-lists), the body is
plain Markdown, and `[[wikilinks]]` draw edges in Obsidian's graph view. Paths
are relative to the vault root and written in place (idempotent — same path
overwrites). No git, no network.

Frontmatter is hand-rolled rather than pulling in PyYAML: our frontmatter is a
small, safe subset (scalars + string-lists), cheap to emit and parse, and it
keeps the dependency list unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def wikilink(slug: str) -> str:
    return f"[[{slug}]]"


def dewikify(value: str) -> str:
    """`[[Postgres Migration]]` -> `Postgres Migration`; passthrough otherwise."""
    m = _WIKILINK_RE.search(value)
    return m.group(1).strip() if m else value


def safe_filename(slug: str) -> str:
    # Obsidian tolerates spaces; only path separators and a few chars are unsafe.
    return re.sub(r'[\\/:*?"<>|]', "-", slug).strip()


# --- YAML (flat subset) -----------------------------------------------------


def _emit_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # everything else is a string: always quote so wikilinks ([[x]]) and colons
    # are never parsed as YAML structure.
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _emit_yaml(data: dict[str, object]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {_emit_scalar(v)}" for v in value)
        else:
            lines.append(f"{key}: {_emit_scalar(value)}")
    return "\n".join(lines)


def _parse_scalar(token: str):
    token = token.strip()
    if token == "[]":
        return []
    if token == "null":
        return None
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if token in {"true", "false"}:
        return token == "true"
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    if re.fullmatch(r"-?\d+\.\d+", token):
        return float(token)
    return token


def _parse_yaml(text: str) -> dict[str, object]:
    out: dict[str, object] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2)
        if rest == "":
            # block list: collect following "  - " items
            items: list[object] = []
            i += 1
            while i < len(lines) and lines[i].startswith("  - "):
                items.append(_parse_scalar(lines[i][4:]))
                i += 1
            out[key] = items
        else:
            out[key] = _parse_scalar(rest)
            i += 1
    return out


# --- notes ------------------------------------------------------------------


class NoteDoc(BaseModel):
    """A note read back from disk — frontmatter + body."""

    frontmatter: dict[str, object] = {}
    body: str = ""

    def get(self, key: str, default=None):
        return self.frontmatter.get(key, default)


class Vault:
    """A directory of Markdown notes. Pure filesystem; no git, no network."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _full(self, path: str) -> Path:
        return self.root / path

    def write_note(self, path: str, frontmatter: dict[str, object], body: str) -> Path:
        full = self._full(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        body = body.rstrip()
        if frontmatter:
            text = f"---\n{_emit_yaml(frontmatter)}\n---\n\n{body}\n"
        else:
            text = f"{body}\n"
        full.write_text(text, encoding="utf-8")
        return full

    def read_note(self, path: str) -> NoteDoc | None:
        full = self._full(path)
        if not full.exists():
            return None
        text = full.read_text(encoding="utf-8")
        fm: dict[str, object] = {}
        body = text
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                fm = _parse_yaml(text[4:end])
                body = text[end + 4 :].lstrip("\n")
        return NoteDoc(frontmatter=fm, body=body)
