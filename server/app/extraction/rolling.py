"""Rolling extraction engine.

A cheap, off-pipeline Claude Haiku call that turns the meeting into a structured
working artifact as it runs. Invoked on an interval (never on the voice path), so
the wake-word brain receives a short context + open tasks instead of the whole
transcript — fewer tokens, less hallucination, lower latency.

Incremental by design: each call gets only the NEW lines since the last pass plus
the PREVIOUS extraction, and returns the merged extraction. Cost scales with new
speech per interval, not with meeting length.

Falls back to a deterministic keyless stub (`_stub_extraction`) so the offline
pipeline still maintains a usable artifact without an API key — mirroring the
verify/meeting brains. The stub only maintains `context`; tasks and preference
candidates require the model and are carried forward untouched offline.
"""

from anthropic import AsyncAnthropic
from anthropic.types import ToolParam, ToolUseBlock
from loguru import logger

from app.config import Settings, get_settings
from app.extraction.schema import RollingExtraction

EXTRACTION_SYSTEM = (
    "You maintain a live, structured extraction of a meeting for an assistant that "
    "may be asked to act on it. You receive the extraction so far and the new "
    "transcript lines since then. Return the UPDATED extraction that folds the new "
    "lines into the old one.\n"
    "- context: tight running prose — who is present, decisions, open questions, "
    "key names. No more than ~150 words.\n"
    "- open_tasks: concrete, addressable units of work someone asked for (e.g. "
    "'create a Jira ticket for the login bug', 'email the deck to Sam'). Carry "
    "forward prior tasks and their status; add new ones as 'pending'. Do not invent "
    "tasks from general discussion.\n"
    "- preference_candidates: durable team practices or preferences worth "
    "remembering across meetings (e.g. 'the team prefers async standups'). Not "
    "one-off tasks."
)

EXTRACTION_TOOL: ToolParam = {
    "name": "rolling_extraction",
    "description": "Return the updated rolling extraction for the meeting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "context": {"type": "string"},
            "open_tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "done"]},
                    },
                    "required": ["text", "status"],
                },
            },
            "preference_candidates": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["context", "open_tasks", "preference_candidates"],
    },
}

# One pooled async client, created lazily so importing this module never needs a key.
_client: AsyncAnthropic | None = None


def _stub_extraction(new_lines: str, prev: RollingExtraction) -> RollingExtraction:
    """Deterministic keyless merge: carry the prior extraction forward and append a
    compressed note of the new lines to `context`. Tasks and preference candidates
    are passed through unchanged (no reliable keyless heuristic). Stable output so
    the offline pipeline and tests are repeatable."""
    note = " | ".join(line.strip() for line in new_lines.splitlines() if line.strip())
    if not note:
        return prev
    entry = f"- {note}"
    context = f"{prev.context}\n{entry}" if prev.context else entry
    return RollingExtraction(
        context=context,
        open_tasks=list(prev.open_tasks),
        preference_candidates=list(prev.preference_candidates),
    )


async def _agent_extraction(
    new_lines: str, prev: RollingExtraction, settings: Settings
) -> RollingExtraction:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    client = _client
    prompt = (
        f"Extraction so far (JSON):\n{prev.model_dump_json(indent=2)}\n\n"
        f"New transcript lines:\n{new_lines}\n\n"
        "Return the updated extraction via the rolling_extraction tool."
    )
    resp = await client.messages.create(
        model=settings.meeting_summary_model,
        max_tokens=1024,
        system=[
            {"type": "text", "text": EXTRACTION_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "rolling_extraction"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if isinstance(block, ToolUseBlock):
            return RollingExtraction.model_validate(block.input)
    raise RuntimeError("extraction response had no tool_use block")


async def extract(
    new_lines: str, prev: RollingExtraction, *, settings: Settings | None = None
) -> RollingExtraction | None:
    """Fold `new_lines` into the rolling extraction.

    Returns the new extraction on success. Returns `None` on failure so the caller
    can KEEP the un-extracted lines and retry next tick instead of consuming them
    into an unchanged extraction (which would silently drop them). Empty input
    returns `prev` unchanged (nothing to do)."""
    settings = settings or get_settings()
    if not new_lines.strip():
        return prev
    if not settings.anthropic_api_key:
        return _stub_extraction(new_lines, prev)
    try:
        result = await _agent_extraction(new_lines, prev, settings)
        return result or prev
    except Exception as exc:  # a background extraction must never break the call
        logger.warning(f"meeting extraction failed, keeping lines for retry: {exc!r}")
        return None
