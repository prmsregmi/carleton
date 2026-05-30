"""Meeting ingestion: Recall.ai transcript + calendar -> normalized Transcript."""

from app.meetings.schema import (
    AttributionSource,
    Attendee,
    MeetingMeta,
    SpeakerAttribution,
    SpeakerMap,
    Transcript,
    Utterance,
)

__all__ = [
    "AttributionSource",
    "Attendee",
    "MeetingMeta",
    "SpeakerAttribution",
    "SpeakerMap",
    "Transcript",
    "Utterance",
]
