"""Scorecard contracts.

Written locally at hangup as the primary, machine-readable eval signal (Cekura
has no public API to pull scores back). `prompt_version` lets Phase 2 group
evals by the context version that produced them.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.anchors import CallerAnchors
from app.verify.schema import Verdict


class ClaimRecord(BaseModel):
    question_id: str | None
    claim: str
    verdict: Verdict
    verify_latency_ms: float = 0.0


class LatencyMetrics(BaseModel):
    stt_ttfb_ms: float | None = None
    llm_ttfb_ms: float | None = None
    tts_ttfb_ms: float | None = None
    avg_verify_latency_ms: float | None = None


class Scorecard(BaseModel):
    call_id: str
    screening_type: str
    prompt_version: str = "v1"
    started_at: datetime
    ended_at: datetime
    anchors: CallerAnchors
    claims: list[ClaimRecord] = Field(default_factory=list)
    contradiction_count: int = 0
    corroborated_count: int = 0
    unconfirmed_count: int = 0
    completion: bool = False
    questions_asked: int = 0
    latency: LatencyMetrics = Field(default_factory=LatencyMetrics)
    rubric_notes: dict[str, str] = Field(default_factory=dict)
    cekura_submitted: bool = False
