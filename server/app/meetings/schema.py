"""Meeting ingest contracts — the normalized transcript handed to extraction.

Recall.ai joins the Google Meet, records, and returns a diarized transcript;
`ingest.py` flattens that (plus the calendar event) into the shapes below. These
are the stable interface between ingestion and extraction — keep field names
stable. Speaker resolution (`roster.py`) reads `Utterance.speaker` + `attendees`
and produces a `SpeakerMap`; the result rides on the Meeting node, not here, so
this layer stays a faithful record of what was said, by which raw label, when.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Attendee(BaseModel):
    """One invitee, from the calendar event. `email` is the canonical Person key."""

    email: str
    display_name: str | None = None
    organizer: bool = False


class Utterance(BaseModel):
    """One diarized turn. `speaker` is Recall's RAW label, not a resolved Person.

    The label may be a real display name ("Ahmed Ismail"), a generic diarization
    tag ("Speaker 0"), or junk ("Ahmed's iPhone"); resolution happens later.
    Timestamps are seconds from the start of the recording.
    """

    speaker: str
    text: str
    start_s: float
    end_s: float


class MeetingMeta(BaseModel):
    """Everything about the meeting except the words. `meeting_id` is the stable
    idempotency key — re-ingesting the same meeting updates, never duplicates."""

    meeting_id: str
    title: str | None = None
    platform: str = "google_meet"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attendees: list[Attendee] = Field(default_factory=list)
    recall_bot_id: str | None = None
    calendar_event_id: str | None = None


class Transcript(BaseModel):
    """The full normalized transcript — what extraction consumes."""

    meta: MeetingMeta
    utterances: list[Utterance] = Field(default_factory=list)

    def speaker_labels(self) -> list[str]:
        """Distinct raw labels present, in first-seen order — the set roster.py
        must resolve."""
        seen: dict[str, None] = {}
        for u in self.utterances:
            seen.setdefault(u.speaker, None)
        return list(seen)


# --- speaker resolution (produced by roster.py, stored on the Meeting node) ---


class AttributionSource(StrEnum):
    """How a raw label was tied to a Person, weakest to strongest."""

    ELIMINATION = "elimination"  # last unmatched label -> last unmatched attendee
    NAME_MATCH = "name_match"  # fuzzy display-name match within the roster
    ALIAS = "alias"  # matched a Person's stored alias from a prior meeting
    HUMAN = "human"  # confirmed in PR review (highest trust; writes back as alias)


class SpeakerAttribution(BaseModel):
    """One raw label -> Person decision. `person` is a Person node slug/email,
    or None when unresolved (then it surfaces in the review draft)."""

    label: str
    person: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: AttributionSource | None = None

    @property
    def resolved(self) -> bool:
        return self.person is not None


class SpeakerMap(BaseModel):
    """roster.py output: every raw label, resolved or flagged for human review."""

    attributions: list[SpeakerAttribution] = Field(default_factory=list)

    def unresolved(self) -> list[str]:
        return [a.label for a in self.attributions if not a.resolved]

    def person_for(self, label: str) -> str | None:
        for a in self.attributions:
            if a.label == label:
                return a.person
        return None
