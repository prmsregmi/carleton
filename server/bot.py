#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""ginjoly — context-parameterized inbound interview/verification voice agent.

Cascade pipeline (STT -> LLM -> TTS) driven by a pipecat-flows graph
(collect_anchors -> questioning -> close). Claims made during questioning are
cross-referenced off the voice path. At hangup a Scorecard is written and the
transcript is submitted to Cekura.

The STT and LLM are selectable (settings.stt_provider / settings.llm_provider)
so the Deepgram+Claude baseline and the NVIDIA Nemotron stack run on the same
pipeline and flow graph — see app/stt: build_stt and app/llm_factory: build_llm.

Run locally (keyless transport):  uv run bot.py -t webrtc
Run on Daily:                     uv run bot.py -t daily
"""

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.config import Settings, get_settings
from app.contexts.hackathon import HACKATHON_CONTEXT
from app.flow import build_flow_manager, make_collect_anchors_node
from app.llm_factory import build_llm
from app.scorecard.cekura import submit_transcript
from app.scorecard.writer import write_scorecard
from app.session import SessionState

load_dotenv(override=True)


def build_stt(settings: Settings):
    """Build the STT service for the configured provider.

    Nemotron Speech Streaming drives its own turn finalization (hard reset on
    VAD stop -> finalized=True TranscriptionFrame), which pairs with the
    smart-turn stop strategy in FilterIncompleteUserTurnStrategies below.
    """
    if settings.stt_provider.lower() == "nemotron":
        from app.services.nvidia_stt import NVidiaWebSocketSTTService

        return NVidiaWebSocketSTTService(
            url=settings.nvidia_asr_url,
            strip_interim_prefix=True,
        )

    from pipecat.services.deepgram.stt import DeepgramSTTService

    return DeepgramSTTService(api_key=settings.deepgram_api_key)


async def _finalize(session: SessionState, context: LLMContext) -> None:
    """Drain pending verifications, write the scorecard, submit to Cekura."""
    await session.drain_verifications()
    card = session.to_scorecard()

    turns: list[dict] = []
    try:
        turns = context.get_messages()
    except Exception as exc:  # transcript is best-effort
        logger.debug(f"could not read transcript: {exc!r}")

    card.cekura_submitted = await submit_transcript(card.call_id, card.prompt_version, turns)
    try:
        write_scorecard(card)
    except Exception as exc:
        logger.warning(f"scorecard write failed: {exc!r}")


async def run_bot(transport: BaseTransport):
    """Assemble the pipeline + flow for one call."""
    settings = get_settings()
    session = SessionState(HACKATHON_CONTEXT)
    logger.info(
        f"starting call {session.call_id} ({session.context.screening_type.value}) "
        f"stt={settings.stt_provider} llm={settings.llm_provider}"
    )

    stt = build_stt(settings)
    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        settings=CartesiaTTSService.Settings(voice=settings.cartesia_voice_id),
    )
    llm = build_llm(settings)

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # Smart-turn endpointing: filter incomplete user turns instead of
            # ending the turn on the first VAD silence. Cuts mid-sentence
            # interruptions and lets the STT's finalization end the turn fast.
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    # PipelineTask (subclass of PipelineWorker) — required by FlowManager.
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[],
    )

    flow_manager = build_flow_manager(task, llm, context_aggregator, transport)

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        # Start the flow once the client can receive audio (greeting plays here).
        await flow_manager.initialize(make_collect_anchors_node(session))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client disconnected; finalizing")
        await _finalize(session, context)
        await task.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(task)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point invoked by the Pipecat dev runner."""
    transport = None

    match runner_args:
        case DailyRunnerArguments():
            transport = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "Ginjoly Agent",
                params=DailyParams(audio_in_enabled=True, audio_out_enabled=True),
            )
        case _:
            # SmallWebRTC and other transports are constructed by the runner and
            # passed through; fall back to the provided transport if present.
            transport = getattr(runner_args, "transport", None)
            if transport is None:
                logger.error(f"Unsupported runner arguments: {type(runner_args)}")
                return

    await run_bot(transport)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
