"""Long-term meeting memory in Obsidian.

Deliberately tiny: the only thing promoted across meetings is the TEAM's
best-practices/preferences (so the agent learns how the team works), seeded into
each meeting's rolling extraction at the start and grown at the end. Plus a raw
per-meeting transcript archive, wikilinked from an index. No per-person nodes, no
decision/action-item graph.
"""

from pathlib import Path

from app.config import get_settings
from app.extraction.obsidian import Vault, safe_filename, wikilink

TEAM_NOTE = "Team/best-practices.md"
ARCHIVE_DIR = "Meetings"
ARCHIVE_INDEX = "Meetings/_index.md"


def _vault(settings=None) -> Vault:
    settings = settings or get_settings()
    return Vault(settings.obsidian_vault_path)


def _norm(line: str) -> str:
    return line.strip().lstrip("-").strip().lower()


def _bullets(body: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in body.splitlines() if ln.strip().startswith("- ")]


def load_team_prefs(*, settings=None) -> str:
    """Return the team best-practices body to seed a meeting's extraction context.
    Empty string when the note doesn't exist yet."""
    doc = _vault(settings).read_note(TEAM_NOTE)
    return doc.body.strip() if doc else ""


def append_team_prefs(new: list[str], *, settings=None) -> None:
    """Dedup-append preference candidates to the team note (case-insensitive). A
    no-op when there's nothing new — so re-running a meeting never duplicates."""
    if not new:
        return
    vault = _vault(settings)
    doc = vault.read_note(TEAM_NOTE)
    existing_body = doc.body.strip() if doc else ""
    seen = {_norm(b) for b in _bullets(existing_body)}
    fresh: list[str] = []
    for pref in new:
        norm = _norm(pref)
        if norm and norm not in seen:
            seen.add(norm)
            fresh.append(pref.strip())
    if not fresh:
        return
    added = "\n".join(f"- {p}" for p in fresh)
    body = f"{existing_body}\n{added}" if existing_body else added
    vault.write_note(TEAM_NOTE, {"type": "team-best-practices"}, body)


def write_transcript_archive(date: str, meeting_id: str, transcript: str, *, settings=None) -> Path:
    """Write the raw meeting transcript as a dated archive note and link it from
    the Meetings index. Unattributed lines — mixed audio has no diarization."""
    vault = _vault(settings)
    slug = safe_filename(f"{date}-{meeting_id}")
    path = vault.write_note(
        f"{ARCHIVE_DIR}/{slug}.md",
        {"type": "meeting-transcript", "date": date, "meeting_id": meeting_id},
        transcript,
    )
    _index_archive(vault, slug)
    return path


def _index_archive(vault: Vault, slug: str) -> None:
    doc = vault.read_note(ARCHIVE_INDEX)
    body = doc.body.strip() if doc else "# Meetings"
    link = f"- {wikilink(slug)}"
    if wikilink(slug) in body:
        return
    vault.write_note(ARCHIVE_INDEX, {"type": "meetings-index"}, f"{body}\n{link}")
