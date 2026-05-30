"""Best-effort submission of the call transcript to Cekura observability.

Cekura has no public API to pull scores back, so this is one-directional: we
push the transcript + the prompt_version tag for dashboard review. Submission
must never block hangup or raise — failures are logged and recorded on the
scorecard as cekura_submitted=False.
"""

import json

import httpx
from loguru import logger

from app.config import get_settings

OBSERVE_URL = "https://api.cekura.ai/observability/v1/observe/"


async def submit_transcript(call_id: str, prompt_version: str, turns: list[dict]) -> bool:
    settings = get_settings()
    if not settings.cekura_api_key:
        logger.info("cekura: no API key set, skipping submission")
        return False

    # Cekura documents the "elevenlabs" turn shape: [{role, message}, ...].
    transcript_json = [
        {"role": t.get("role", "assistant"), "message": t.get("content", "")} for t in turns
    ]
    data = {
        "agent": settings.cekura_agent_id,
        "call_id": call_id,
        "transcript_type": "elevenlabs",
        "transcript_json": json.dumps(transcript_json),
        "prompt_version": prompt_version,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                OBSERVE_URL,
                headers={"X-CEKURA-API-KEY": settings.cekura_api_key},
                data=data,
            )
        if resp.status_code // 100 == 2:
            logger.info(f"cekura: submitted call {call_id}")
            return True
        logger.warning(f"cekura: non-2xx ({resp.status_code}): {resp.text[:200]}")
        return False
    except Exception as exc:  # best-effort; never propagate
        logger.warning(f"cekura: submission failed: {exc!r}")
        return False
