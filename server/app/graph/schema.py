"""Knowledge-graph node contracts — the typed form of an Obsidian Markdown note.

Each node serializes to one Markdown file: the structured fields below become
YAML frontmatter, `body` becomes the prose beneath it, and `links` become
`[[wikilinks]]` (the graph's edges). `vault.py` does that serialization; this
module only defines the shapes, so the data model has one home.

Design rules that keep the network efficient, encoded here:
- Every node has a stable `slug` (its filename) so merges are idempotent.
- People are keyed by `email`; `aliases` accumulate so noisy speaker labels
  auto-resolve next time (see meetings.roster).
- Action items reference a Person by slug, never by raw transcript label — the
  speaker map is resolved before any node is written.
"""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.meetings.schema import SpeakerAttribution


class NodeType(StrEnum):
    PERSON = "person"
    MEETING = "meeting"
    DECISION = "decision"
    ACTION_ITEM = "action_item"
    PROJECT = "project"


class GraphNode(BaseModel):
    """Common to every note. `slug` is the filename stem and identity key."""

    slug: str
    type: NodeType
    title: str
    links: list[str] = Field(default_factory=list)  # outgoing [[wikilinks]] (slugs)
    body: str = ""  # free prose under the frontmatter
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Person(GraphNode):
    """One teammate. The personalization anchor — grows over time, never per-call.

    `email` is the canonical key; `aliases` are display names/labels seen in
    transcripts so future meetings resolve speakers without re-asking.
    """

    type: NodeType = NodeType.PERSON
    email: str
    aliases: list[str] = Field(default_factory=list)
    role: str | None = None
    team: str | None = None
    expertise: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    current_projects: list[str] = Field(default_factory=list)  # project slugs
    open_action_items: list[str] = Field(default_factory=list)  # action_item slugs
    comms_style: str | None = None  # learned, e.g. "terse, prefers async"


class Meeting(GraphNode):
    """One meeting. Carries the resolved speaker map for audit + idempotent re-runs."""

    type: NodeType = NodeType.MEETING
    meeting_id: str  # mirrors MeetingMeta.meeting_id
    held_on: date | None = None
    attendees: list[str] = Field(default_factory=list)  # person slugs
    speaker_map: list[SpeakerAttribution] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)  # decision slugs
    action_items: list[str] = Field(default_factory=list)  # action_item slugs
    transcript_ref: str | None = None  # path/URL to the raw transcript


class Decision(GraphNode):
    """A decision the meeting reached. Connective tissue across meetings."""

    type: NodeType = NodeType.DECISION
    statement: str
    decided_by: list[str] = Field(default_factory=list)  # person slugs
    project: str | None = None  # project slug
    source_meeting: str | None = None  # meeting slug
    rationale: str | None = None
    decided_on: date | None = None


class ActionItemStatus(StrEnum):
    PROPOSED = "proposed"  # extracted, awaiting human review (the default)
    APPROVED = "approved"  # human kept it in the PR
    REJECTED = "rejected"  # human removed it (e.g. the "breakfast ticket")
    SYNCED = "synced"  # pushed to the external tracker (Jira — stubbed this branch)


class ActionItem(GraphNode):
    """A task. The normalized unit a Jira adapter later consumes (kept tracker-agnostic)."""

    type: NodeType = NodeType.ACTION_ITEM
    task: str
    assignee: str | None = None  # person slug (resolved, never a raw label)
    project: str | None = None  # project slug
    due: date | None = None
    status: ActionItemStatus = ActionItemStatus.PROPOSED
    source_meeting: str | None = None  # meeting slug
    source_decision: str | None = None  # decision slug, if it implements one
    external_ref: str | None = None  # e.g. Jira key — populated downstream, stub here
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Project(GraphNode):
    """A project/system (e.g. [[Postgres Migration]], [[Auth Service]]).

    The hub most links route through; aggressive reuse here is what stops the
    vault from fragmenting into a hairball (see graph.resolve)."""

    type: NodeType = NodeType.PROJECT
    name: str
    aliases: list[str] = Field(default_factory=list)  # "the migration", "pg move"
    description: str | None = None
    status: str | None = None
    people: list[str] = Field(default_factory=list)  # person slugs
