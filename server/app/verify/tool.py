"""The in-call `verify_claim` tool.

flows function handlers don't expose the raw result_callback, so the plan's
two-phase is_final pattern isn't available here. The robust equivalent: fire
the brain as a background task and return immediately, so the voice loop never
blocks. The verdict is recorded into the session for the scorecard; surfacing
it back into the conversation mid-call is a later enhancement.
"""

import asyncio
import time

from loguru import logger
from pipecat_flows import FlowArgs, FlowManager, FlowsFunctionSchema

from app.config import get_settings
from app.session import SessionState
from app.verify.brain import run_verification
from app.verify.schema import Verdict, VerifyClaimInput


async def _run_and_record(session: SessionState, claim: str, question_id: str | None) -> None:
    started = time.monotonic()
    try:
        verdict = await asyncio.wait_for(
            run_verification(
                VerifyClaimInput(
                    claim=claim,
                    anchors=session.anchors,
                    question_id=question_id,
                    call_id=session.call_id,
                )
            ),
            timeout=get_settings().verify_timeout_secs,
        )
    except TimeoutError:
        verdict = Verdict.unconfirmed("verification timed out")
    except Exception as exc:  # never let a background failure break the call
        logger.warning(f"verification error: {exc!r}")
        verdict = Verdict.unconfirmed(f"verification error: {type(exc).__name__}")

    latency_ms = (time.monotonic() - started) * 1000
    session.record_claim(claim, verdict, question_id=question_id, latency_ms=latency_ms)
    logger.info(f"verified claim ({verdict.label.value}, {latency_ms:.0f}ms): {claim[:80]}")


def make_verify_claim_schema(session: SessionState) -> FlowsFunctionSchema:
    async def verify_claim(args: FlowArgs, flow_manager: FlowManager):
        claim = args["claim"]
        question_id = args.get("question_id")
        session.questions_asked += 1
        # Background task — do NOT await; keep the conversation responsive.
        # Tracked so the scorecard can drain in-flight checks at hangup.
        bg = asyncio.create_task(_run_and_record(session, claim, question_id))
        session.pending_verifications.append(bg)
        return {"status": "checking", "message": "Noted, I'll look into that."}, None

    return FlowsFunctionSchema(
        name="verify_claim",
        description=(
            "Record a concrete factual claim the caller just made (a project, "
            "role, tool, or contribution) so it can be cross-referenced against "
            "their public presence. Call this once per substantive claim."
        ),
        properties={
            "claim": {
                "type": "string",
                "description": "The caller's claim, in one concise sentence.",
            },
            "question_id": {
                "type": "string",
                "description": "The id of the question this claim answers, if known.",
            },
        },
        required=["claim"],
        handler=verify_claim,
        cancel_on_interruption=False,
    )
