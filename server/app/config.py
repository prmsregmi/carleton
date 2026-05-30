"""Typed settings loaded from the environment / .env.

Every key is optional so the module imports cleanly even when a given
integration is not configured yet; callers check for the specific key they
need. Phase 1 voice requires DEEPGRAM/ANTHROPIC/CARTESIA; verification adds
SCRAPINGDOG/GITHUB; eval adds CEKURA; Phase 2 memory adds SUPERMEMORY.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Voice pipeline (Phase 1 core) ---
    deepgram_api_key: str | None = None
    cartesia_api_key: str | None = None
    cartesia_voice_id: str = "71a7ad14-091c-4e8e-a314-022ece01c121"
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


@lru_cache
def get_settings() -> Settings:
    """Process-wide singleton."""
    return Settings()
