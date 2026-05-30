"""Verification contracts.

A Verdict expresses CORROBORATION, not proof: ScrapingDog/GitHub return public
data only, so we score how strongly a found profile agrees with the caller's
anchors and never assert ownership we can't support. The disclaimer is baked in.
"""

from enum import Enum, StrEnum

from pydantic import BaseModel, Field

from app.anchors import CallerAnchors

DISCLAIMER = "Corroboration against public data only; not proof of identity or ownership."


class VerdictLabel(StrEnum):
    CORROBORATED = "corroborated"
    UNCONFIRMED = "unconfirmed"
    CONTRADICTED = "contradicted"


class VerifyClaimInput(BaseModel):
    """Handed from the voice LLM tool to the off-path brain."""

    claim: str
    anchors: CallerAnchors
    question_id: str | None = None
    call_id: str


class Evidence(BaseModel):
    source: str  # "google" | "linkedin" | "x" | "github"
    url: str | None = None
    snippet: str
    field_matched: str | None = None  # which anchor this corroborates


class Verdict(BaseModel):
    label: VerdictLabel
    confidence: float = Field(ge=0.0, le=1.0)
    profile_match_score: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_profile_url: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    reasoning: str = ""
    disclaimer: str = DISCLAIMER

    @classmethod
    def unconfirmed(cls, reason: str) -> "Verdict":
        """Fallback used on timeout / error — never blocks the call."""
        return cls(
            label=VerdictLabel.UNCONFIRMED,
            confidence=0.0,
            profile_match_score=0.0,
            reasoning=reason,
        )
