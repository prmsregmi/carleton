"""Obsidian vault I/O — the only place that knows nodes are Markdown files.

Each GraphNode becomes one note: structured fields render as YAML frontmatter,
relationships render as `[[wikilinks]]` (both in frontmatter, so they show in
Obsidian's Properties pane, and inline in a `## Related` block, so the graph view
draws edges on every Obsidian version). Files are foldered by type and named by
`slug`, which is both the filename and the stable identity key — so writing the
same node twice overwrites in place (idempotent merges depend on this).

Frontmatter is hand-rolled rather than pulling in PyYAML: our frontmatter is a
flat map of scalars and string-lists, which is a small, safe subset to emit and
parse, and it keeps the dependency list unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from app.graph.schema import (
    ActionItem,
    Decision,
    GraphNode,
    Meeting,
    NodeType,
    Person,
    Project,
)

# Per-type folder names — keep the vault navigable in Obsidian's file tree.
_FOLDERS: dict[NodeType, str] = {
    NodeType.PERSON: "People",
    NodeType.MEETING: "Meetings",
    NodeType.DECISION: "Decisions",
    NodeType.ACTION_ITEM: "Action Items",
    NodeType.PROJECT: "Projects",
}

# Which fields hold slugs that must render as wikilinks (so they become edges).
# Value is True for list-valued fields, False for scalar.
_LINK_FIELDS: dict[type, dict[str, bool]] = {
    Person: {"current_projects": True, "open_action_items": True},
    Meeting: {"attendees": True, "decisions": True, "action_items": True},
    Decision: {"decided_by": True, "project": False, "source_meeting": False},
    ActionItem: {
        "assignee": False,
        "project": False,
        "source_meeting": False,
        "source_decision": False,
    },
    Project: {"people": True},
}

# Fields rendered specially in the body, never in frontmatter.
_BODY_ONLY = {"body", "speaker_map"}
# Slug is the filename, links are rendered in ## Related — don't repeat as scalars.
_FRONTMATTER_SKIP = {"slug", "links"}

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def wikilink(slug: str) -> str:
    return f"[[{slug}]]"


def dewikify(value: str) -> str:
    """`[[Postgres Migration]]` -> `Postgres Migration`; passthrough otherwise."""
    m = _WIKILINK_RE.search(value)
    return m.group(1).strip() if m else value


def _safe_filename(slug: str) -> str:
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


# --- serialization ----------------------------------------------------------


def _frontmatter_dict(node: GraphNode) -> dict[str, object]:
    raw = node.model_dump(mode="json")
    links = _LINK_FIELDS.get(type(node), {})
    fm: dict[str, object] = {}
    for key, value in raw.items():
        if key in _BODY_ONLY or key in _FRONTMATTER_SKIP:
            continue
        if key in links:
            if value in (None, [], ""):
                continue
            value = [wikilink(v) for v in value] if links[key] else wikilink(value)
        # Drop empties to keep notes tidy; keep numbers/bools (0/False are signal).
        if value is None or value == "" or value == []:
            continue
        fm[key] = value
    return fm


def _related_block(node: GraphNode) -> str:
    """Inline wikilinks guaranteeing graph edges regardless of Obsidian version."""
    links = _LINK_FIELDS.get(type(node), {})
    lines: list[str] = []
    for key, is_list in links.items():
        value = getattr(node, key, None)
        if not value:
            continue
        targets = value if is_list else [value]
        rendered = ", ".join(wikilink(t) for t in targets)
        lines.append(f"- **{key.replace('_', ' ')}**: {rendered}")
    for extra in node.links:
        lines.append(f"- {wikilink(extra)}")
    if not lines:
        return ""
    return "## Related\n" + "\n".join(lines)


def _speaker_map_block(node: Meeting) -> str:
    if not node.speaker_map:
        return ""
    lines = ["## Speaker map"]
    for a in node.speaker_map:
        if a.person:
            src = a.source.value if a.source else "?"
            lines.append(f"- `{a.label}` → {wikilink(a.person)} ({src}, {a.confidence:.2f})")
        else:
            lines.append(f"- `{a.label}` → _unresolved_")
    return "\n".join(lines)


def render(node: GraphNode) -> str:
    """Full Markdown for one note: frontmatter + title + body + sections."""
    fm = _emit_yaml(_frontmatter_dict(node))
    parts = [f"---\n{fm}\n---", f"# {node.title}"]
    if node.body.strip():
        parts.append(node.body.strip())
    if isinstance(node, Meeting):
        if block := _speaker_map_block(node):
            parts.append(block)
    if block := _related_block(node):
        parts.append(block)
    return "\n\n".join(parts) + "\n"


# --- vault ------------------------------------------------------------------


class NodeDoc(BaseModel):
    """A note read back from disk — frontmatter + body, not a reconstructed node.

    Resolution (graph.resolve) needs identity fields (slug, email, aliases, name),
    not the full typed node, so we return this lightweight view and avoid lossy
    reconstruction of body-only fields like a Meeting's speaker map.
    """

    slug: str
    type: NodeType | None = None
    frontmatter: dict[str, object] = {}
    body: str = ""

    def get(self, key: str, default=None):
        return self.frontmatter.get(key, default)


class Vault:
    """A directory of Markdown notes. Pure filesystem; no git, no network."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path_for(self, node: GraphNode) -> Path:
        folder = _FOLDERS[node.type]
        return self.root / folder / f"{_safe_filename(node.slug)}.md"

    def write(self, node: GraphNode) -> Path:
        path = self.path_for(node)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(node), encoding="utf-8")
        return path

    def write_all(self, nodes: list[GraphNode]) -> list[Path]:
        return [self.write(n) for n in nodes]

    def read(self, path: str | Path) -> NodeDoc:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        fm: dict[str, object] = {}
        body = text
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                fm = _parse_yaml(text[4:end])
                body = text[end + 4 :].lstrip("\n")
        node_type = None
        raw_type = fm.get("type")
        if isinstance(raw_type, str):
            try:
                node_type = NodeType(raw_type)
            except ValueError:
                node_type = None
        return NodeDoc(slug=path.stem, type=node_type, frontmatter=fm, body=body)

    def list(self, node_type: NodeType | None = None) -> list[NodeDoc]:
        if node_type is not None:
            folder = self.root / _FOLDERS[node_type]
            paths = sorted(folder.glob("*.md")) if folder.exists() else []
        else:
            paths = sorted(self.root.rglob("*.md"))
        return [self.read(p) for p in paths]
