"""Obsidian long-term memory: the slim note writer and the team-prefs/archive
layer built on it. All filesystem, no network — tests run against a tmp vault.
"""

from app.extraction.memory import (
    append_team_prefs,
    load_team_prefs,
    write_transcript_archive,
)
from app.extraction.obsidian import Vault, wikilink


class _Settings:
    def __init__(self, root):
        self.obsidian_vault_path = str(root)


# --- Vault writer: frontmatter + body round-trip ---
def test_write_then_read_round_trips_frontmatter_and_body(tmp_path):
    v = Vault(tmp_path)
    v.write_note("Team/note.md", {"type": "team", "tags": ["a", "b"]}, "the body")
    doc = v.read_note("Team/note.md")
    assert doc is not None
    assert doc.frontmatter["type"] == "team"
    assert doc.frontmatter["tags"] == ["a", "b"]
    assert doc.body.strip() == "the body"


def test_write_note_without_frontmatter_writes_plain_body(tmp_path):
    v = Vault(tmp_path)
    v.write_note("Meetings/x.md", {}, "raw transcript line\nanother")
    doc = v.read_note("Meetings/x.md")
    assert doc is not None
    assert doc.frontmatter == {}
    assert "raw transcript line" in doc.body


def test_read_missing_note_returns_none(tmp_path):
    assert Vault(tmp_path).read_note("nope.md") is None


def test_wikilink_emits_obsidian_link():
    assert wikilink("2026-05-30-abc") == "[[2026-05-30-abc]]"


# --- Team best-practices memory ---
def test_load_team_prefs_empty_when_absent(tmp_path):
    assert load_team_prefs(settings=_Settings(tmp_path)) == ""


def test_append_team_prefs_creates_note_and_loads_back(tmp_path):
    s = _Settings(tmp_path)
    append_team_prefs(["prefer async standups", "ship behind flags"], settings=s)
    body = load_team_prefs(settings=s)
    assert "prefer async standups" in body
    assert "ship behind flags" in body


def test_append_team_prefs_dedups_case_insensitively(tmp_path):
    s = _Settings(tmp_path)
    append_team_prefs(["Prefer async standups"], settings=s)
    append_team_prefs(["prefer async standups", "new practice"], settings=s)
    body = load_team_prefs(settings=s)
    # The repeated pref appears once; the genuinely new one is added.
    assert body.lower().count("prefer async standups") == 1
    assert "new practice" in body


def test_append_team_prefs_empty_list_is_noop(tmp_path):
    s = _Settings(tmp_path)
    append_team_prefs([], settings=s)
    assert load_team_prefs(settings=s) == ""


# --- Transcript archive ---
def test_write_transcript_archive_writes_note_and_indexes_it(tmp_path):
    s = _Settings(tmp_path)
    path = write_transcript_archive("2026-05-30", "abc123", "alice: hi\nbob: hey", settings=s)
    assert path.exists()
    doc = Vault(tmp_path).read_note("Meetings/2026-05-30-abc123.md")
    assert doc is not None
    assert "alice: hi" in doc.body
    # The index links to the archived meeting note.
    index = Vault(tmp_path).read_note("Meetings/_index.md")
    assert index is not None
    assert wikilink("2026-05-30-abc123") in index.body
