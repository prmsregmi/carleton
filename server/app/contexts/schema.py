"""Context contracts — the per-session payload that parameterizes the agent.

These models are the stable interface between Phase 1 (the live agent) and
Phase 2 (memory, flywheel, auto-improvement). Keep field names stable.
"""

from enum import Enum, StrEnum

from pydantic import BaseModel, Field


class ScreeningType(StrEnum):
    HACKATHON_APPLICANT = "hackathon_applicant"
    JOB_PHONE_SCREEN = "job_phone_screen"
    EVENT_LEAD = "event_lead"


class AnchorSpec(BaseModel):
    """One identity anchor the agent must collect from the caller."""

    key: str  # "name" | "company" | "email" | "profile_url"
    prompt: str  # how the agent asks for it
    required: bool = True
    validate_as: str | None = None  # "email" | "url" | None


class QuestionItem(BaseModel):
    """A topical question drawn from the context's bank."""

    id: str
    text: str
    expects_claim: bool = True  # answer should yield a verifiable factual claim
    follow_up_hint: str | None = None


class RubricItem(BaseModel):
    """A scoring criterion used when assessing the caller."""

    id: str
    criterion: str
    weight: float = 1.0


class Context(BaseModel):
    """Loaded per session; defines what this call screens for."""

    screening_type: ScreeningType
    display_name: str
    intro_script: str
    required_anchors: list[AnchorSpec]
    question_bank: list[QuestionItem]
    rubric: list[RubricItem]
    close_script: str
    max_questions: int = 5
    corroboration_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
