"""Rolling meeting summary.

A cheap, off-pipeline Claude Haiku call that compresses the meeting as it runs.
It is invoked on an interval (and never on the voice path), so the wake-word
brain receives a short running summary plus the few un-summarized lines instead
of the whole transcript — fewer tokens, less hallucination, lower latency.

Incremental by design: each call gets only the NEW lines since the last summary
plus the PREVIOUS summary, and returns the merged summary. So cost scales with
new speech per interval, not with meeting length.

Falls back to a deterministic keyless stub (`_stub_summary`) so the offline
pipeline still maintains a usable summary without an API key — mirroring the
verify/meeting brains.
"""

from anthropic import AsyncAnthropic
from loguru import logger

from app.config import Settings, get_settings

SUMMARY_SYSTEM = (
    "You maintain a running summary of a live meeting for an assistant that may "
    "be asked to act on it. You receive the summary so far and the new transcript "
    "lines since then. Return an updated summary that folds the new lines into the "
    "old one. Keep it tight: who is present, decisions, open questions, and any "
    "tasks or names mentioned. Plain prose or short dashes, no preamble, no more "
    "than ~150 words. Return ONLY the summary text."
)

# One pooled async client, created lazily so importing this module never needs a key.
_client: AsyncAnthropic | None = None


def _stub_summary(new_lines: str, prev_summary: str) -> str:
    """Deterministic keyless merge: carry the prior summary forward and append a
    compressed note of the new lines. Stable output so the offline pipeline and
    tests are repeatable."""
    note = " | ".join(line.strip() for line in new_lines.splitlines() if line.strip())
    if not note:
        return prev_summary
    entry = f"- {note}"
    return f"{prev_summary}\n{entry}" if prev_summary else entry


async def _agent_summary(new_lines: str, prev_summary: str, settings: Settings) -> str:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    client = _client
    prompt = (
        f"Summary so far:\n{prev_summary or '(none yet)'}\n\n"
        f"New transcript lines:\n{new_lines}\n\n"
        "Return the updated running summary."
    )
    resp = await client.messages.create(
        model=settings.meeting_summary_model,
        max_tokens=400,
        system=[
            {"type": "text", "text": SUMMARY_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [getattr(block, "text", "") for block in resp.content if block.type == "text"]
    return "".join(parts).strip()


async def summarize(
    new_lines: str, prev_summary: str, *, settings: Settings | None = None
) -> str | None:
    """Update the running summary with `new_lines`.

    Returns the new summary on success. Returns `None` on failure so the caller
    can KEEP the un-summarized lines and retry next tick instead of consuming
    them into an unchanged summary (which would silently drop them). Empty input
    returns `prev_summary` unchanged (nothing to do)."""
    settings = settings or get_settings()
    if not new_lines.strip():
        return prev_summary
    if not settings.anthropic_api_key:
        return _stub_summary(new_lines, prev_summary)
    try:
        result = await _agent_summary(new_lines, prev_summary, settings)
        return result or prev_summary
    except Exception as exc:  # a background summary must never break the call
        logger.warning(f"meeting summary failed, keeping lines for retry: {exc!r}")
        return None
