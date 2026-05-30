"""Per-connection state for one meeting.

Memory is a running summary plus a tail of un-summarized lines. The rolling
summarizer (off the voice path, on an interval) folds the tail into the summary;
between ticks the wake-word brain reads `context_for_brain()` = summary + tail,
so it sees long-range context cheaply AND the last few seconds verbatim.

Also tracks the actions taken and a short memory of the bot's own recent speech
for the self-echo filter on the mixed stream.
"""

import uuid
from collections import deque
from datetime import UTC, datetime

from loguru import logger


def _new_call_id() -> str:
    return f"{int(datetime.now(UTC).timestamp())}-{uuid.uuid4().hex[:8]}"


class MeetingSessionState:
    def __init__(self, *, tail_cap: int = 200):
        self.call_id = _new_call_id()
        # Un-summarized transcript lines. Intentionally UNBOUNDED at the deque
        # level: a summarize pass snapshots the front N lines and `apply_summary`
        # pops exactly those, so the front must not shift under it mid-flight. The
        # backlog is instead capped (oldest-dropped, logged) inside apply_summary,
        # which only runs between summarize passes — never during one.
        self.tail: deque[str] = deque()
        self._tail_cap = tail_cap
        self.running_summary: str = ""
        self.actions: list[tuple[str, str]] = []
        # Bot's own recent spoken lines (lower-cased) for the echo filter.
        self.recent_tts: deque[str] = deque(maxlen=8)

    def add_line(self, text: str) -> None:
        self.tail.append(text)

    def take_unsummarized(self) -> tuple[str, int]:
        """Snapshot the current tail as (text, line_count). The count is what a
        later `apply_summary` consumes — lines that arrive after this call stay
        in the tail and are not dropped."""
        lines = list(self.tail)
        return "\n".join(lines), len(lines)

    def apply_summary(self, summary: str, consumed: int) -> None:
        """Install the new running summary and drop exactly `consumed` lines from
        the front of the tail (the ones it summarized), preserving anything that
        arrived during summarization. Then enforce the backlog cap, dropping the
        OLDEST surplus (with a warning) if summarization can't keep up — never the
        freshest lines the brain still needs."""
        self.running_summary = summary
        for _ in range(min(consumed, len(self.tail))):
            self.tail.popleft()
        overflow = len(self.tail) - self._tail_cap
        if overflow > 0:
            for _ in range(overflow):
                self.tail.popleft()
            logger.warning(
                f"meeting tail over cap ({self._tail_cap}); dropped {overflow} "
                "un-summarized line(s) — summarizer is falling behind"
            )

    def context_for_brain(self) -> str:
        """Summary-then-tail, oldest to newest. Empty string until anything is seen."""
        parts: list[str] = []
        if self.running_summary:
            parts.append(f"Summary so far:\n{self.running_summary}")
        if self.tail:
            parts.append("Since then:\n" + "\n".join(self.tail))
        return "\n\n".join(parts)

    def note_spoken(self, text: str) -> None:
        self.recent_tts.append(text.strip().lower())

    def record_action(self, request: str, summary: str) -> None:
        self.actions.append((request, summary))
