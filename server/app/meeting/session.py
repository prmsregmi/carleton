"""Per-connection state for one meeting.

Memory is a rolling extraction (context + open tasks + preference candidates)
plus a tail of un-extracted lines. The rolling extractor (off the voice path, on
an interval) folds the tail into the extraction; between ticks the wake-word
brain reads `context_for_brain()` = context + pending tasks + tail, so it sees
long-range context cheaply AND the last few seconds verbatim.

Also tracks the actions taken and a short memory of the bot's own recent speech
for the self-echo filter on the mixed stream.
"""

import uuid
from collections import deque
from datetime import UTC, datetime

from loguru import logger

from app.extraction.schema import RollingExtraction


def _new_call_id() -> str:
    return f"{int(datetime.now(UTC).timestamp())}-{uuid.uuid4().hex[:8]}"


class MeetingSessionState:
    def __init__(self, *, tail_cap: int = 200):
        self.call_id = _new_call_id()
        # Un-extracted transcript lines. Intentionally UNBOUNDED at the deque
        # level: an extract pass snapshots the front N lines and `apply_extraction`
        # pops exactly those, so the front must not shift under it mid-flight. The
        # backlog is instead capped (oldest-dropped, logged) inside apply_extraction,
        # which only runs between extract passes — never during one.
        self.tail: deque[str] = deque()
        self._tail_cap = tail_cap
        self.extraction = RollingExtraction()
        # Full transcript, never popped — the raw record archived to Obsidian at
        # meeting end (the tail above is only the un-extracted working set).
        self.transcript_log: list[str] = []
        self.actions: list[tuple[str, str]] = []
        # Bot's own recent spoken lines (lower-cased) for the echo filter.
        self.recent_tts: deque[str] = deque(maxlen=8)

    def add_line(self, text: str) -> None:
        self.tail.append(text)
        self.transcript_log.append(text)

    def full_transcript(self) -> str:
        """The whole meeting, oldest to newest — for the end-of-meeting archive."""
        return "\n".join(self.transcript_log)

    def take_unsummarized(self) -> tuple[str, int]:
        """Snapshot the current tail as (text, line_count). The count is what a
        later `apply_extraction` consumes — lines that arrive after this call stay
        in the tail and are not dropped."""
        lines = list(self.tail)
        return "\n".join(lines), len(lines)

    def apply_extraction(self, extraction: RollingExtraction, consumed: int) -> None:
        """Install the new rolling extraction and drop exactly `consumed` lines from
        the front of the tail (the ones it folded in), preserving anything that
        arrived during extraction. Then enforce the backlog cap, dropping the
        OLDEST surplus (with a warning) if extraction can't keep up — never the
        freshest lines the brain still needs."""
        self.extraction = extraction
        for _ in range(min(consumed, len(self.tail))):
            self.tail.popleft()
        overflow = len(self.tail) - self._tail_cap
        if overflow > 0:
            for _ in range(overflow):
                self.tail.popleft()
            logger.warning(
                f"meeting tail over cap ({self._tail_cap}); dropped {overflow} "
                "un-extracted line(s) — extractor is falling behind"
            )

    def mark_task_done(self, text: str) -> bool:
        """Flip the first matching pending task to done. Matching is case-insensitive
        and substring-tolerant either way, so a verbatim wake-word request lines up
        with the extractor's paraphrase of the same task. Returns whether one matched
        (best-effort: the wake word executes regardless)."""
        norm = text.strip().lower()
        for task in self.extraction.open_tasks:
            if task.status != "pending":
                continue
            low = task.text.lower()
            if norm and (norm in low or low in norm):
                task.status = "done"
                return True
        return False

    def context_for_brain(self) -> str:
        """Context, then pending tasks, then the fresh tail — oldest to newest.
        Empty string until anything is seen."""
        parts: list[str] = []
        if self.extraction.context:
            parts.append(f"Summary so far:\n{self.extraction.context}")
        pending = [t.text for t in self.extraction.open_tasks if t.status == "pending"]
        if pending:
            parts.append("Open tasks:\n" + "\n".join(f"- {t}" for t in pending))
        if self.tail:
            parts.append("Since then:\n" + "\n".join(self.tail))
        return "\n\n".join(parts)

    def note_spoken(self, text: str) -> None:
        self.recent_tts.append(text.strip().lower())

    def record_action(self, request: str, summary: str) -> None:
        self.actions.append((request, summary))
