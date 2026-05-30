"""Meeting pipeline assembly.

Same transport/STT/TTS infra as the interview agent, but with NO conversational
LLM in the pipeline: the brain runs off-path (like verification). The flow is
simply listen -> gate -> (on address) speak.

    transport.input() -> VADProcessor -> STT -> WakeNameGate -> TTS -> transport.output()

VADProcessor gives natural interruption (a participant talking over the bot
stops its TTS). STT (Deepgram default) provides the final transcriptions the
gate keys off.
"""

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.transports.base_transport import BaseTransport

from app.config import get_settings
from app.meeting.gate import WakeNameGate
from app.meeting.session import MeetingSessionState
from app.stt_factory import build_stt


def build_meeting_pipeline(
    transport: BaseTransport,
    *,
    on_transcript=None,
    on_assistant=None,
    on_extraction=None,
) -> tuple[PipelineWorker, MeetingSessionState]:
    """Assemble the meeting pipeline for one bridge connection.

    on_transcript(text) / on_assistant(text) / on_extraction(extraction) are
    optional async callbacks the gate fires so a UI (the meeting frontend) can
    render the live transcript, the bot's replies, and the rolling extraction
    (context + tasks). Leave them None for a headless run.
    """
    settings = get_settings()
    session = MeetingSessionState(tail_cap=settings.meeting_tail_max_lines)

    stt = build_stt(settings)
    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        settings=CartesiaTTSService.Settings(voice=settings.cartesia_voice_id),
    )
    vad = SileroVADAnalyzer(
        sample_rate=settings.meeting_sample_rate,
        # Slightly aggressive stop to limit echo tails on the mixed stream.
        params=VADParams(stop_secs=0.6),
    )
    gate = WakeNameGate(
        session,
        settings.wake_names,
        speak_ack=settings.meeting_speak_ack,
        self_echo_filter=settings.meeting_self_echo_filter,
        summary_interval=settings.meeting_summary_interval_secs,
        on_transcript=on_transcript,
        on_assistant=on_assistant,
        on_extraction=on_extraction,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(vad_analyzer=vad),
            stt,
            gate,
            tts,
            transport.output(),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=settings.meeting_sample_rate,
            audio_out_sample_rate=settings.meeting_playback_sample_rate,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[],
    )
    return worker, session
