"""Wake-name gate.

Sits after STT in the meeting pipeline. The bot is a passive listener: it never
forwards a conversational turn to an LLM. It only acts when a final transcription
contains one of its wake names — then it strips the wake prefix, dispatches the
request to the brain (off-pipeline), and speaks the result back via TTSSpeakFrame.

Mixed-stream caveat: the bridge re-captures the bot's own injected TTS, so a
self-echo filter discards transcriptions that closely match the bot's recent
speech. The real fix is the Playwright side muting capture during playback; this
is the in-process backstop.
"""

import asyncio
from datetime import UTC, datetime
from difflib import SequenceMatcher

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.extraction.memory import (
    append_team_prefs,
    load_team_prefs,
    write_transcript_archive,
)
from app.extraction.rolling import extract
from app.meeting.brain import handle_request
from app.meeting.session import MeetingSessionState

_ECHO_RATIO = 0.8
_STRIP = " ,.:;-—!?"
# Leading conversational filler skipped before the wake name, so "hey ginjoly"
# and "ok so ginny" still count as being addressed.
_FILLERS = {"hey", "hi", "hello", "ok", "okay", "so", "um", "uh", "yo", "there", "alright"}


class WakeNameGate(FrameProcessor):
    def __init__(
        self,
        session: MeetingSessionState,
        wake_names: list[str],
        *,
        speak_ack: bool = True,
        self_echo_filter: bool = True,
        summary_interval: float = 0.0,
        on_transcript=None,
        on_assistant=None,
        on_extraction=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._session = session
        self._wake_names = [w.lower() for w in wake_names]
        self._speak_ack = speak_ack
        self._self_echo_filter = self_echo_filter
        # >0 enables the rolling extraction loop (folds the tail into the rolling
        # extraction every `summary_interval` seconds); 0 keeps the raw tail only.
        self._summary_interval = summary_interval
        # Optional async callbacks to surface activity to a UI (the meeting
        # frontend): on_transcript(text) per final line, on_assistant(text) for
        # the bot's reply, on_extraction(extraction) after each rolling-extraction
        # tick so the dashboard can render context + tasks. All no-op when None.
        self._on_transcript = on_transcript
        self._on_assistant = on_assistant
        self._on_extraction = on_extraction
        self._bg_tasks: set[asyncio.Task] = set()
        self._summary_task: asyncio.Task | None = None

    def _request_after_wake(self, text: str) -> str | None:
        """Return the request when the utterance is ADDRESSED to the bot.

        The bot is addressed only when a wake name is the first meaningful token
        (after optional filler like "hey"/"ok so"). A wake name buried mid-
        sentence ("I mentioned ginjoly earlier") is NOT an address, so it can't
        trigger a spurious task in a meeting where people discuss the bot.
        """
        tokens = text.split()
        i = 0
        while i < len(tokens) and tokens[i].strip(_STRIP).lower() in _FILLERS:
            i += 1
        if i >= len(tokens) or tokens[i].strip(_STRIP).lower() not in self._wake_names:
            return None
        remainder = " ".join(tokens[i + 1 :]).strip(_STRIP)
        return remainder or None

    def _is_echo(self, text: str) -> bool:
        if not self._self_echo_filter:
            return False
        t = text.strip().lower()
        for spoken in self._session.recent_tts:
            if not spoken:
                continue
            if t in spoken or spoken in t or SequenceMatcher(None, t, spoken).ratio() > _ECHO_RATIO:
                return True
        return False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # Start the rolling extractor once the pipeline is live. Framework-managed
        # task (create_task) so Pipecat tracks and tears it down with the worker.
        if isinstance(frame, StartFrame):
            self._seed_team_prefs()
            if self._summary_interval > 0 and self._summary_task is None:
                self._summary_task = self.create_task(self._summary_loop())
            await self.push_frame(frame, direction)
            return

        # Pipeline is ending: cancel any in-flight brain task so it can't push a
        # frame into a dead pipeline (or keep the SDK subprocess burning tokens).
        if isinstance(frame, (EndFrame, CancelFrame)):
            self._cancel_bg_tasks()
            await self.push_frame(frame, direction)
            return

        # Drop interim transcripts entirely so partial text can't trigger a wake.
        if isinstance(frame, InterimTranscriptionFrame):
            return

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if not text:
                return
            if self._is_echo(text):
                logger.debug(f"meeting: dropped self-echo: {text[:60]!r}")
                return
            self._session.add_line(text)
            await self._notify(self._on_transcript, text)
            request = self._request_after_wake(text)
            if request:
                logger.info(f"meeting: addressed -> {request[:80]!r}")
                await self._dispatch(request)
            # Never forward transcriptions downstream — there is no conversational
            # LLM in this pipeline; the brain handles addressed requests off-path.
            return

        # System/control/audio frames must flow through (StartFrame, EndFrame,
        # interruptions, etc.). Inbound audio is dropped at the serializer, so
        # forwarding it here causes no feedback loop.
        await self.push_frame(frame, direction)

    async def _summary_loop(self) -> None:
        """Periodically fold the un-extracted tail into the rolling extraction.

        Off the voice path: a cheap Haiku call (or keyless stub) processes only
        the NEW lines plus the prior extraction, so the brain later reads a short
        context + open tasks + fresh tail instead of the whole transcript. Snapshot
        the line count BEFORE extracting so lines arriving mid-call are never dropped.
        """
        while True:
            await asyncio.sleep(self._summary_interval)
            new_lines, count = self._session.take_unsummarized()
            if count == 0:
                continue
            updated = await extract(new_lines, self._session.extraction)
            if updated is None:
                # Extraction failed; keep the lines in the tail and retry next tick.
                continue
            self._session.apply_extraction(updated, count)
            logger.debug(f"meeting extraction updated from {count} line(s)")
            await self._notify(self._on_extraction, self._session.extraction)

    async def _dispatch(self, request: str) -> None:
        if self._speak_ack:
            await self._speak("On it.")
        transcript = self._session.context_for_brain()

        async def _run():
            try:
                summary = await handle_request(request, transcript)
                # Flip a matching extracted task to done so the end-of-meeting
                # batch runner doesn't re-execute what the wake word just did.
                self._session.mark_task_done(request)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let a task failure kill the pipeline
                logger.warning(f"meeting brain error: {exc!r}")
                summary = "Sorry, I couldn't complete that."
            self._session.record_action(request, summary)
            await self._notify(self._on_assistant, summary)
            await self._speak(summary)

        # Track the task so it can be cancelled if the bridge disconnects mid-call.
        task = asyncio.create_task(_run())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _notify(self, cb, payload) -> None:
        if cb is None:
            return
        try:
            await cb(payload)
        except Exception as exc:  # a UI hiccup must never break the pipeline
            logger.debug(f"meeting notify failed: {exc!r}")

    def _cancel_bg_tasks(self) -> None:
        for task in list(self._bg_tasks):
            task.cancel()
        # Best-effort stop on the End/Cancel fast path (sync context); cleanup()
        # does the awaited teardown below.
        if self._summary_task is not None:
            self._summary_task.cancel()

    def _seed_team_prefs(self) -> None:
        """Seed the rolling extraction with the team's learned best-practices so the
        brain starts the meeting already knowing how the team works. Best-effort:
        a vault read must never delay the pipeline going live."""
        try:
            prefs = load_team_prefs()
        except Exception as exc:
            logger.debug(f"meeting: team-prefs seed skipped: {exc!r}")
            return
        if prefs and not self._session.extraction.context:
            self._session.extraction.context = f"Team best practices:\n{prefs}"

    def _persist_memory(self) -> None:
        """At meeting end: promote new preference candidates to the team note and
        archive the raw transcript. Best-effort — never crash teardown."""
        try:
            append_team_prefs(self._session.extraction.preference_candidates)
        except Exception as exc:
            logger.warning(f"meeting: team-prefs append failed: {exc!r}")
        try:
            transcript = self._session.full_transcript()
            if transcript.strip():
                date = datetime.now(UTC).date().isoformat()
                write_transcript_archive(date, self._session.call_id, transcript)
        except Exception as exc:
            logger.warning(f"meeting: transcript archive failed: {exc!r}")

    async def cleanup(self):
        self._cancel_bg_tasks()
        # Persist long-term memory before teardown completes (off the voice path).
        self._persist_memory()
        # Authoritative teardown: await the framework cancel so an in-flight
        # extract() HTTP call is actually torn down before the processor goes.
        if self._summary_task is not None:
            await self.cancel_task(self._summary_task)
            self._summary_task = None
        await super().cleanup()

    async def _speak(self, text: str) -> None:
        # Record before speaking so the echo filter can suppress the re-capture.
        self._session.note_spoken(text)
        await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)
