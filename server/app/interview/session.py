"""Per-call mutable runtime state.

Lives for the duration of one call. Accumulates anchors, verified claims, and
timing, then renders an immutable Scorecard at hangup. Kept as a plain object
(not pydantic) because it is mutated throughout the call from multiple handlers.
"""

from datetime import UTC, datetime, timezone

from app.interview.anchors import CallerAnchors
from app.interview.contexts.schema import Context
from app.interview.scorecard.schema import ClaimRecord, LatencyMetrics, Scorecard
from app.interview.verify.schema import Verdict, VerdictLabel


def _now() -> datetime:
    return datetime.now(UTC)


def new_call_id() -> str:
    """Short, sortable, collision-resistant enough for a session."""
    import uuid

    return f"{int(_now().timestamp())}-{uuid.uuid4().hex[:8]}"


class SessionState:
    def __init__(self, context: Context, *, call_id: str | None = None):
        self.context = context
        self.call_id = call_id or new_call_id()
        self.prompt_version = "v1"
        self.started_at = _now()
        self.ended_at: datetime | None = None
        self.anchors = CallerAnchors()
        self.claims: list[ClaimRecord] = []
        self.questions_asked = 0
        self.completed = False  # reached the close node cleanly
        # Background verification tasks still in flight; drained before scorecard.
        self.pending_verifications: list = []

    async def drain_verifications(self, timeout: float | None = None) -> None:
        """Let in-flight background verifications finish recording before hangup.

        Defaults to verify_timeout_secs + 2 s so we never cut off a verification
        that is still within its own deadline (the old 10 s hard-code was shorter
        than the default 20 s verify timeout, silently discarding late verdicts).
        """
        if timeout is None:
            from app.config import get_settings
            timeout = get_settings().verify_timeout_secs + 2.0
        import asyncio

        pending = [t for t in self.pending_verifications if not t.done()]
        if not pending:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=timeout
            )
        except TimeoutError:
            pass

    # --- anchors ---
    def set_anchors(self, **fields: str | None) -> None:
        merged = self.anchors.model_dump()
        for key, value in fields.items():
            if value:
                merged[key] = value
        self.anchors = CallerAnchors(**merged)

    # --- claims ---
    def record_claim(
        self, claim: str, verdict: Verdict, *, question_id: str | None, latency_ms: float
    ) -> None:
        self.claims.append(
            ClaimRecord(
                question_id=question_id,
                claim=claim,
                verdict=verdict,
                verify_latency_ms=latency_ms,
            )
        )

    # --- scorecard ---
    def to_scorecard(self) -> Scorecard:
        self.ended_at = self.ended_at or _now()
        labels = [c.verdict.label for c in self.claims]
        verify_latencies = [c.verify_latency_ms for c in self.claims if c.verify_latency_ms]
        avg_verify = sum(verify_latencies) / len(verify_latencies) if verify_latencies else None
        return Scorecard(
            call_id=self.call_id,
            screening_type=self.context.screening_type.value,
            prompt_version=self.prompt_version,
            started_at=self.started_at,
            ended_at=self.ended_at,
            anchors=self.anchors,
            claims=self.claims,
            contradiction_count=labels.count(VerdictLabel.CONTRADICTED),
            corroborated_count=labels.count(VerdictLabel.CORROBORATED),
            unconfirmed_count=labels.count(VerdictLabel.UNCONFIRMED),
            completion=self.completed,
            questions_asked=self.questions_asked,
            latency=LatencyMetrics(avg_verify_latency_ms=avg_verify),
        )
