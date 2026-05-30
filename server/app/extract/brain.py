"""Extraction brain.

The function the pipeline calls to turn one meeting into structured context:
`run_extraction(transcript, context) -> ExtractionResult`. It feeds Claude the
transcript *plus the attendees' existing Person nodes and known projects*, so
extraction is personalized — tasks attribute to the right teammate, person notes
get enriched, and mentions resolve to EXISTING slugs instead of spawning
duplicates (the first line of defense for an efficient network; `resolve.py`
finalizes the rest).

Mirrors `verify.brain`: same Agent-SDK skeleton and strict-JSON contract, and a
deterministic keyless stub so the offline pipeline runs without an API key.
"""

import json
import re

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from loguru import logger

from app.config import get_settings
from app.extract.schema import (
    EntityMention,
    ExtractedActionItem,
    ExtractedDecision,
    ExtractionContext,
    ExtractionResult,
    PersonDelta,
)
from app.meetings.schema import Transcript

# Typed list fields and their item model — used to salvage a partially-bad payload.
_LIST_ITEM_MODELS = {
    "decisions": ExtractedDecision,
    "action_items": ExtractedActionItem,
    "mentions": EntityMention,
    "person_deltas": PersonDelta,
}

EXTRACT_SYSTEM = """You extract structured, actionable context from a meeting transcript.
You are personalized: you are given the attendees' profiles and the projects that already
exist, and you MUST use them.

Rules:
1. Attribute every decision and action item to a real attendee by name when the transcript
   supports it. Use the profiles to disambiguate (e.g. the person who owns a project).
2. Reuse existing things: if a mention matches a known project (by name or alias), set its
   `resolved_slug`/`project` to that EXACT slug. Do not invent a new name for something that
   already exists.
3. Ground every action item and decision in a `source_quote` copied from the transcript.
   If you cannot quote it, do not emit it. Never manufacture a task from casual chatter
   (no "schedule breakfast" tickets).
4. `person_deltas` are small and additive — what this one meeting newly revealed about a
   person (a responsibility taken on, expertise shown, a project joined). Do not restate
   their whole profile.
5. List any speaker label you could not tie to a known attendee in `unresolved_speakers`.

Return ONLY a JSON object (no prose, no code fences) with exactly these keys:
{"summary": "<2-3 sentence meeting summary>",
 "decisions": [{"statement": "...", "decided_by": ["<name>"], "rationale": "<or null>",
                "related_project": "<existing slug or null>", "source_quote": "..."}],
 "action_items": [{"task": "...", "assignee": "<name or null>", "due": "<text or null>",
                   "project": "<existing slug or null>", "confidence": 0.0-1.0,
                   "source_quote": "..."}],
 "mentions": [{"surface": "...", "type": "person|project|system|tool",
               "resolved_slug": "<existing slug or null>", "confidence": 0.0-1.0}],
 "person_deltas": [{"person": "<name>", "summary": "<one line or null>",
                    "new_expertise": [], "new_responsibilities": [], "new_projects": []}],
 "open_questions": ["..."],
 "unresolved_speakers": ["..."]}"""


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def render_transcript(transcript: Transcript) -> str:
    """Flatten utterances into a readable, speaker-labelled script."""
    return "\n".join(
        f"[{_mmss(u.start_s)}] {u.speaker}: {u.text}" for u in transcript.utterances
    )


def _render_context(ctx: ExtractionContext) -> str:
    lines = []
    if ctx.meeting_title:
        lines.append(f"Meeting: {ctx.meeting_title}" + (f" ({ctx.held_on})" if ctx.held_on else ""))
    if ctx.attendees:
        lines.append("\nAttendees (their existing profiles):")
        for p in ctx.attendees:
            bits = [f"- {p.name} [{p.slug}]"]
            if p.role:
                bits.append(f"role: {p.role}")
            if p.expertise:
                bits.append(f"expertise: {', '.join(p.expertise)}")
            if p.responsibilities:
                bits.append(f"owns: {', '.join(p.responsibilities)}")
            if p.current_projects:
                bits.append(f"projects: {', '.join(p.current_projects)}")
            lines.append("; ".join(bits))
    if ctx.known_projects:
        lines.append("\nExisting projects (reuse these slugs, don't re-create):")
        for pr in ctx.known_projects:
            alias = f" (aka {', '.join(pr.aliases)})" if pr.aliases else ""
            lines.append(f"- {pr.slug}{alias}")
    return "\n".join(lines) if lines else "(no prior context)"


def _result_from_data(data: dict, meeting_id: str) -> ExtractionResult:
    data = dict(data)
    data["meeting_id"] = meeting_id
    try:
        return ExtractionResult.model_validate(data)
    except Exception as exc:  # one malformed item shouldn't lose the whole meeting
        logger.warning(f"extraction JSON failed validation, salvaging item-by-item: {exc!r}")
        # Validate each typed-list item independently, keeping the good ones —
        # a single bad mention must not cost us the action items.
        for field, item_model in _LIST_ITEM_MODELS.items():
            raw = data.get(field)
            if not isinstance(raw, list):
                data[field] = []
                continue
            kept = []
            for item in raw:
                try:
                    kept.append(item_model.model_validate(item).model_dump())
                except Exception:
                    logger.warning(f"dropping malformed {field} item: {item!r}")
            data[field] = kept
        try:
            return ExtractionResult.model_validate(data)
        except Exception:
            return ExtractionResult.empty(meeting_id, "extraction returned unparseable JSON")


async def _agent_extraction(transcript: Transcript, ctx: ExtractionContext) -> ExtractionResult:
    settings = get_settings()
    options = ClaudeAgentOptions(
        system_prompt=EXTRACT_SYSTEM,
        model=settings.anthropic_model,
        max_turns=1,  # single structured pass; post-meeting batch, no tool loop
        permission_mode="bypassPermissions",
    )
    prompt = (
        f"{_render_context(ctx)}\n\n"
        f"Transcript:\n{render_transcript(transcript)}\n\n"
        "Extract the meeting context and return only the JSON object."
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
        return ExtractionResult.empty(transcript.meta.meeting_id, "brain returned no parseable JSON")
    return _result_from_data(data, transcript.meta.meeting_id)


async def _stub_extraction(transcript: Transcript, ctx: ExtractionContext) -> ExtractionResult:
    """Keyless-dev fallback: deterministic, clearly labelled, exercises the pipeline."""
    text = " ".join(u.text for u in transcript.utterances).lower()
    meeting_id = transcript.meta.meeting_id
    if "postgres" in text or "migrat" in text:
        speaker = transcript.utterances[0].speaker if transcript.utterances else "unknown"
        project = ctx.known_projects[0].slug if ctx.known_projects else "Postgres Migration"
        return ExtractionResult(
            meeting_id=meeting_id,
            summary="[stub] Team agreed to migrate the primary database to Postgres.",
            decisions=[
                {
                    "statement": "Migrate the primary database from SQL to Postgres.",
                    "decided_by": [speaker],
                    "related_project": project,
                    "source_quote": "[stub] derived without an API key",
                }
            ],
            action_items=[
                {
                    "task": "Migrate the primary database from SQL to Postgres.",
                    "assignee": speaker,
                    "project": project,
                    "confidence": 0.6,
                    "source_quote": "[stub] derived without an API key",
                }
            ],
            person_deltas=[{"person": speaker, "new_responsibilities": ["database migration"]}],
        )
    return ExtractionResult.empty(meeting_id, "[stub] no API key; nothing actionable detected")


async def run_extraction(transcript: Transcript, context: ExtractionContext) -> ExtractionResult:
    """Extract structured context from a meeting, personalized to its attendees.

    The single entry point the pipeline (and `merge`) calls. Falls back to a
    deterministic stub with no API key, and to an empty result on error — it
    never raises into the pipeline.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        return await _stub_extraction(transcript, context)
    try:
        return await _agent_extraction(transcript, context)
    except Exception as exc:
        logger.warning(f"agent extraction failed, returning empty: {exc!r}")
        return ExtractionResult.empty(transcript.meta.meeting_id, f"brain error: {type(exc).__name__}")
