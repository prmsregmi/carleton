"""Verification brain.

A Claude Agent SDK agent that corroborates a caller's claim against public
sources (ScrapingDog Google + LinkedIn, GitHub) using the identity anchors the
caller gave. It NEVER asserts ownership it cannot support — the output is a
corroboration verdict, not proof.

Falls back to a deterministic stub when no verification credentials are
configured, so keyless local dev still exercises the full call path. Keeps the
exact `run_verification(vin) -> Verdict` signature the tool depends on.
"""

import asyncio
import json
import re

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
)
from loguru import logger

from app.config import get_settings
from app.verify.corroboration import fallback_match_score
from app.verify.schema import Evidence, Verdict, VerdictLabel, VerifyClaimInput
from app.verify.tools_github import github_user
from app.verify.tools_scrapingdog import google_search, linkedin_profile

VERIFY_SYSTEM = """You verify a caller's factual claim by CORROBORATING it against
public data. You DO NOT prove identity or ownership.

Procedure:
1. Use google_search "<name> <company> linkedin OR github OR x" to find candidate profiles.
2. For the single strongest candidate, fetch details: github_user (free, prefer it for
   builders) and/or linkedin_profile (50 credits — at most once, only if clearly the person).
3. Score profile_match_score in [0,1]: how strongly the profile's public fields agree with
   the caller's anchors (name, company, email domain, location).
4. Only attribute a profile to the caller if profile_match_score >= the given threshold.
5. Compare the claim to the corroborating evidence and decide:
   - corroborated: public evidence supports the claim AND a profile is attributed
   - contradicted: public evidence conflicts with the claim
   - unconfirmed: not enough attributable evidence either way
Never assert ownership you cannot support.

Return ONLY a JSON object (no prose, no code fences) with exactly these keys:
{"label": "corroborated|unconfirmed|contradicted",
 "confidence": 0.0-1.0,
 "profile_match_score": 0.0-1.0,
 "matched_profile_url": "<url or null>",
 "evidence": [{"source": "...", "url": "<or null>", "snippet": "...", "field_matched": "<or null>"}],
 "reasoning": "<one or two sentences>"}"""

_TOOL_NAMES = [
    "mcp__verify__google_search",
    "mcp__verify__linkedin_profile",
    "mcp__verify__github_user",
]


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    # strip code fences if present
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _verdict_from_data(data: dict, vin: VerifyClaimInput) -> Verdict:
    label = str(data.get("label", "unconfirmed")).lower()
    if label not in {v.value for v in VerdictLabel}:
        label = "unconfirmed"
    score = data.get("profile_match_score")
    if score is None:
        score = fallback_match_score(vin.anchors.model_dump(), json.dumps(data.get("evidence", [])))
    evidence = [
        Evidence(
            source=str(e.get("source", "unknown")),
            url=e.get("url"),
            snippet=str(e.get("snippet", "")),
            field_matched=e.get("field_matched"),
        )
        for e in data.get("evidence", [])
        if isinstance(e, dict)
    ]
    return Verdict(
        label=VerdictLabel(label),
        confidence=float(data.get("confidence", 0.0) or 0.0),
        profile_match_score=float(score or 0.0),
        matched_profile_url=data.get("matched_profile_url"),
        evidence=evidence,
        reasoning=str(data.get("reasoning", "")),
    )


async def _agent_verification(vin: VerifyClaimInput) -> Verdict:
    settings = get_settings()
    server = create_sdk_mcp_server(
        "verify", "1.0.0", [google_search, linkedin_profile, github_user]
    )
    options = ClaudeAgentOptions(
        system_prompt=VERIFY_SYSTEM,
        model=settings.anthropic_model,
        max_turns=12,
        mcp_servers={"verify": server},
        allowed_tools=_TOOL_NAMES,
        permission_mode="bypassPermissions",
    )
    prompt = (
        f"Claim: {vin.claim}\n"
        f"Caller anchors: {vin.anchors.model_dump_json(exclude_none=True)}\n"
        f"Attribution threshold (profile_match_score): "
        f"{settings_threshold(vin)}\n"
        "Verify the claim and return only the Verdict JSON."
    )

    result_text = ""
    accumulated = ""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        accumulated += block.text
            elif isinstance(msg, ResultMessage):
                result_text = msg.result or ""

    data = _extract_json(result_text) or _extract_json(accumulated)
    if not data:
        return Verdict.unconfirmed("brain returned no parseable verdict")
    return _verdict_from_data(data, vin)


def settings_threshold(vin: VerifyClaimInput) -> float:
    # The corroboration threshold travels on the Context, not the claim input;
    # default to a sensible 0.6 here (the questioning prompt already encodes it).
    return 0.6


async def _stub_verification(vin: VerifyClaimInput) -> Verdict:
    """Keyless-dev fallback: canned, deterministic, clearly labelled."""
    await asyncio.sleep(1.5)
    claim = vin.claim.lower()
    if any(k in claim for k in ("github", "open source", "open-source", "repo")):
        return Verdict(
            label=VerdictLabel.CORROBORATED,
            confidence=0.8,
            profile_match_score=0.85,
            matched_profile_url="https://github.com/example",
            evidence=[
                Evidence(
                    source="github",
                    url="https://github.com/example",
                    snippet="[stub] public repos consistent with the claim",
                    field_matched="name",
                )
            ],
            reasoning="[stub verdict] no verification credentials configured",
        )
    return Verdict.unconfirmed("[stub verdict] no verification credentials configured")


async def run_verification(vin: VerifyClaimInput) -> Verdict:
    settings = get_settings()
    have_creds = bool(
        settings.scrapingdog_api_key or settings.github_token or settings.anthropic_api_key
    )
    if not have_creds:
        return await _stub_verification(vin)
    try:
        return await _agent_verification(vin)
    except Exception as exc:
        logger.warning(f"agent verification failed, returning unconfirmed: {exc!r}")
        return Verdict.unconfirmed(f"brain error: {type(exc).__name__}")
