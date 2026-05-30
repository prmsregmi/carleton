"""Typed settings loaded from the environment / .env.

Every key is optional so the module imports cleanly even when a given
integration is not configured yet; callers check for the specific key they
need. Phase 1 voice requires DEEPGRAM/ANTHROPIC/CARTESIA; verification adds
SCRAPINGDOG/GITHUB; eval adds CEKURA; Phase 2 memory adds SUPERMEMORY.
"""

import uuid
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# British Reading Lady — used when CARTESIA_VOICE_ID is unset or invalid.
DEFAULT_CARTESIA_VOICE = "71a7ad14-091c-4e8e-a314-022ece01c121"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Voice pipeline (Phase 1 core) ---
    deepgram_api_key: str | None = None
    cartesia_api_key: str | None = None
    cartesia_voice_id: str = DEFAULT_CARTESIA_VOICE
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"

    # --- Stack selection (Phase 1) ---
    # Swap the cascade's ear/brain without touching the flow graph.
    #   stt_provider: "deepgram" (baseline) | "nemotron"
    #   llm_provider: "anthropic" (baseline) | "nemotron" | "openai" | "nim"
    stt_provider: str = "deepgram"
    llm_provider: str = "anthropic"

    # --- Swappable LLM alternatives ---
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    nvidia_api_key: str | None = None
    nvidia_model: str = "nvidia/llama-3.1-nemotron-70b-instruct"

    # --- Nemotron (NVIDIA open models on AWS) ---
    # Default to the hackathon-provided AWS fleet; override these two URLs to
    # point at your own AWS vLLM / ASR deployment (no code change needed).
    nvidia_asr_url: str = "ws://44.241.251.184:8080"
    nemotron_llm_url: str = "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1"
    nemotron_llm_model: str = "nvidia/nemotron-3-super"
    nemotron_llm_api_key: str = "EMPTY"  # vLLM ignores unless served with --api-key
    nemotron_enable_thinking: bool = False  # keep OFF for low-latency voice

    # --- Verification brain ---
    scrapingdog_api_key: str | None = None
    github_token: str | None = None

    # --- Eval ---
    cekura_api_key: str | None = None
    cekura_agent_id: str = "ginjoly"

    # --- Transport (hosted demo) ---
    daily_api_key: str | None = None

    # --- Phase 2 memory ---
    supermemory_api_key: str | None = None

    # --- Behaviour knobs ---
    verify_timeout_secs: float = 20.0

    # --- Meeting task agent (second app; shares transport/STT/TTS) ---
    # The bot listens passively in a meeting and only acts when addressed by one
    # of these wake names. Comma-separated in the env; parsed via wake_names.
    meeting_wake_names: str = "onion"
    # Display name the Meet bot joins under; the web UI reads it via /api/config.
    meeting_bot_name: str = "Onion"
    meeting_ws_host: str = "0.0.0.0"
    meeting_ws_port: int = 7861
    # Mixed Google-Meet audio arrives as raw 16-bit PCM at this rate (mono);
    # the bot's TTS is sent back at the same rate for the Playwright bridge.
    meeting_sample_rate: int = 16000
    # Speak a short ack ("On it.") while the MCP task runs in the background.
    meeting_speak_ack: bool = True
    # Safety cap on un-summarized transcript lines held as the tail (the rolling
    # summarizer normally drains these well before the cap is reached; oldest are
    # dropped with a warning only if it falls behind).
    meeting_tail_max_lines: int = 200
    # Rolling-summary memory: a cheap Haiku call folds the tail into a running
    # summary on this interval, so the brain gets summary + fresh tail instead of
    # the whole transcript. Set the interval to 0 to disable and use the raw tail.
    meeting_summary_interval_secs: float = 300.0
    meeting_summary_model: str = "claude-haiku-4-5"
    # Discard transcriptions that echo the bot's own recent speech (mixed stream
    # re-captures injected TTS). Backstop; the bridge should also mute on playback.
    meeting_self_echo_filter: bool = True
    meeting_max_turns: int = 8

    # --- External MCP servers for the meeting agent (URL + bearer token) ---
    # Each tool is registered only when BOTH its url and token are present.
    jira_mcp_url: str | None = None
    jira_mcp_token: str | None = None
    slack_mcp_url: str | None = None
    slack_mcp_token: str | None = None
    gmail_mcp_url: str | None = None
    gmail_mcp_token: str | None = None

    @property
    def wake_names(self) -> list[str]:
        """Lower-cased wake names, comma-separated from the env."""
        return [n.strip().lower() for n in self.meeting_wake_names.split(",") if n.strip()]

    @field_validator("cartesia_voice_id", mode="before")
    @classmethod
    def _coerce_voice_id(cls, v):
        """Cartesia requires a UUID. Fall back to the default if the env value
        is empty or not a UUID (e.g. a stray inline comment from .env.example)."""
        if not v:
            return DEFAULT_CARTESIA_VOICE
        candidate = str(v).strip()
        try:
            uuid.UUID(candidate)
        except ValueError:
            return DEFAULT_CARTESIA_VOICE
        return candidate


@lru_cache
def get_settings() -> Settings:
    """Process-wide singleton."""
    return Settings()
