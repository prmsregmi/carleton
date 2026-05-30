"""Bridge between the Playwright Meet capture and the ginjoly meeting agent.

`meet_bot.join_meet` captures the meeting's mixed audio as raw 16 kHz mono PCM
and pushes each chunk onto an asyncio.Queue. `MeetTransport` turns that queue
into a pipecat input transport; the meeting agent's pipeline (VAD -> STT ->
WakeNameGate -> brain/MCP -> TTS) runs in-process over it.

The bot speaks back: `BrowserAudioSink` forwards the TTS `OutputAudioRawFrame`s
onto a playback queue that the Playwright side injects as the bot's microphone.
Because Meet mixes the bot's own voice back into the captured stream, a shared
`PlaybackState` suppresses capture while the bot is speaking so it doesn't
transcribe itself.
"""

import asyncio
import os
import sys
import time

# connector/bridge/); make `app`/`bot` importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.base_transport import BaseTransport


class PlaybackState:
    """Shared speak flag so capture is suppressed while the bot talks.

    The mixed Meet stream feeds the bot its own injected TTS, so without this the
    bot would transcribe itself. The sink extends `speaking_until` on every output
    audio frame; the input source drops frames while it's active, including a
    short tail past the last frame to swallow the playback's reverb on the mix.
    """

    def __init__(self, tail: float = 0.4):
        self._tail = tail
        self._until = 0.0

    def mark(self):
        self._until = time.monotonic() + self._tail

    def active(self) -> bool:
        return time.monotonic() < self._until


class MeetAudioSource(FrameProcessor):
    """Pumps raw PCM from the Meet queue into the pipeline as InputAudioRawFrames.

    The pump starts when the pipeline's StartFrame arrives (rather than relying
    on a transport start hook) and stops on End/Cancel. A None on the queue means
    the Meet ended, which we translate into an EndFrame to drain the pipeline.
    """

    def __init__(
        self, queue: asyncio.Queue, sample_rate: int = 16000, playback_state=None, **kwargs
    ):
        super().__init__(**kwargs)
        self._queue = queue
        self._sample_rate = sample_rate
        self._playback_state = playback_state
        self._pump: asyncio.Task | None = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame) and self._pump is None:
            self._pump = asyncio.create_task(self._run())
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._pump:
            self._pump.cancel()

    async def _run(self):
        while True:
            data = await self._queue.get()
            if data is None:
                await self.push_frame(EndFrame())
                break
            # Drop captured audio while the bot is speaking so its own injected
            # voice (mixed back by Meet) never reaches STT.
            if self._playback_state and self._playback_state.active():
                continue
            await self.push_frame(
                InputAudioRawFrame(audio=data, sample_rate=self._sample_rate, num_channels=1)
            )


class BrowserAudioSink(FrameProcessor):
    """Forwards the bot's TTS audio to the Playwright page (which injects it as
    the bot's microphone). Control/other frames flow through unchanged."""

    def __init__(self, playback_queue: asyncio.Queue | None = None, playback_state=None, **kwargs):
        super().__init__(**kwargs)
        self._playback_queue = playback_queue
        self._playback_state = playback_state

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame):
            if self._playback_state:
                self._playback_state.mark()
            if self._playback_queue is not None:
                self._playback_queue.put_nowait(frame.audio)
            return
        await self.push_frame(frame, direction)


class MeetTransport(BaseTransport):
    """Minimal transport bridging the Meet audio queue into pipecat (in) and the
    bot's TTS back out to the page (out)."""

    def __init__(
        self,
        audio_queue: asyncio.Queue,
        sample_rate: int = 16000,
        playback_queue: asyncio.Queue | None = None,
        playback_state=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._source = MeetAudioSource(
            audio_queue, sample_rate, playback_state, name="MeetAudioSource"
        )
        self._sink = BrowserAudioSink(playback_queue, playback_state, name="BrowserAudioSink")

    def input(self) -> FrameProcessor:
        return self._source

    def output(self) -> FrameProcessor:
        return self._sink


async def run_meet_pipeline(
    audio_queue: asyncio.Queue,
    playback_queue: asyncio.Queue | None = None,
    on_transcript=None,
    on_assistant=None,
    on_extraction=None,
    on_session=None,
):
    """Run the ginjoly meeting agent over audio coming from `audio_queue`.

    on_transcript(text) fires for every final transcription line; on_assistant(text)
    fires with the bot's reply after it handles an addressed request;
    on_extraction(extraction) fires after each rolling-extraction tick. on_session
    is called once with the live MeetingSessionState so the caller can drive the
    end-of-meeting batch runner against it. TTS audio is forwarded onto
    `playback_queue` for the Playwright side to speak into the Meet.
    """
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=True)

    from pipecat.workers.runner import WorkerRunner

    from app.config import get_settings
    from app.meeting.pipeline import build_meeting_pipeline

    settings = get_settings()
    playback_state = PlaybackState()
    transport = MeetTransport(
        audio_queue,
        sample_rate=settings.meeting_sample_rate,
        playback_queue=playback_queue,
        playback_state=playback_state,
    )

    worker, session = build_meeting_pipeline(
        transport,
        on_transcript=on_transcript,
        on_assistant=on_assistant,
        on_extraction=on_extraction,
    )
    if on_session is not None:
        on_session(session)

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()
