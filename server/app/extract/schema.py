"""Extraction contracts — the structured draft the extraction brain returns.

This is the PRE-resolution output: the agent reads the full transcript (plus the
existing graph for context) and emits decisions, action items, entity mentions,
and per-person deltas. Assignees and mentions may still be raw transcript labels
or surface strings here — `graph.resolve` ties them to Person/Project slugs, and
`graph.merge` writes the result behind a human PR review. Mirrors the role
`scorecard.schema` played for the screening agent: the machine-readable artifact
of one session.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class MentionType(StrEnum):
    PERSON = "person"
    PROJECT = "project"
    SYSTEM = "system"
    TOOL = "tool"


class EntityMention(BaseModel):
    """A thing referred to in the meeting, for graph linking. `resolved_slug` is
    filled by graph.resolve; None means 'propose a new node' (flagged in review)."""

    surface: str  # as said: "the auth service", "pg migration", "Sarah"
    type: MentionType
    resolved_slug: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractedDecision(BaseModel):
    statement: str
    decided_by: list[str] = Field(default_factory=list)  # speaker labels or names
    rationale: str | None = None
    related_project: str | None = None  # surface string, resolved later
    source_quote: str | None = None  # grounding span from the transcript


class ExtractedActionItem(BaseModel):
    """A proposed task. `assignee` is whatever the transcript implies (a label or
    name); resolution to a Person slug happens downstream."""

    task: str
    assignee: str | None = None
    due: str | None = None  # natural-language ok ("next sprint"); normalized later
    project: str | None = None  # surface string
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_quote: str | None = None  # grounding span — defends against hallucinated tickets


class PersonDelta(BaseModel):
    """What this meeting taught us about a person — merged into their Person node.

    Additive and small on purpose: a meeting nudges the personal model, it does
    not rewrite it. `person` is a speaker label/name, resolved before applying.
    """

    person: str
    summary: str | None = None  # one line folded into the node body
    new_expertise: list[str] = Field(default_factory=list)
    new_responsibilities: list[str] = Field(default_factory=list)
    new_projects: list[str] = Field(default_factory=list)  # surface strings


class ExtractionResult(BaseModel):
    """The extraction brain's complete, strict-JSON output for one meeting."""

    meeting_id: str
    decisions: list[ExtractedDecision] = Field(default_factory=list)
    action_items: list[ExtractedActionItem] = Field(default_factory=list)
    mentions: list[EntityMention] = Field(default_factory=list)
    person_deltas: list[PersonDelta] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)  # surfaced, not invented
    unresolved_speakers: list[str] = Field(default_factory=list)  # need human in review
    summary: str = ""  # short meeting summary for the Meeting node body

    @classmethod
    def empty(cls, meeting_id: str, reason: str = "") -> "ExtractionResult":
        """Fallback when the brain returns nothing parseable — never fabricates."""
        return cls(meeting_id=meeting_id, summary=reason)
