"""Swappable in-pipeline LLM.

Baseline is Anthropic Claude. `provider="nemotron"` swaps in NVIDIA
Nemotron-3-Super served by vLLM (OpenAI-compatible) — the judges' open-model
path; `provider="nim"` uses an NVIDIA NIM hosted model; `provider="openai"` is
a fallback if Anthropic in-pipeline tool-calling misbehaves. The provider is
read from `settings.llm_provider` unless overridden, so the rest of the
pipeline and the flow wiring are identical across providers.

ginjoly uses LLMContextAggregatorPair, so pipecat-flows runs the
UniversalLLMAdapter: any OpenAILLMService subclass (VLLMOpenAILLMService) works
with the existing FlowsFunctionSchema tools without a flow-graph change.

The system prompt is intentionally NOT set here: with pipecat-flows the active
node's `role_message` owns the system instruction (it would overwrite anything
set on the constructor), so the factory only configures the model + credentials.
"""

from app.config import Settings


def build_llm(settings: Settings, *, provider: str | None = None):
    """Build the in-pipeline LLM service for the configured provider."""
    provider = (provider or settings.llm_provider).lower()

    if provider in ("nemotron", "vllm"):
        # Nemotron-3-Super via vLLM's OpenAI-compatible /v1 (Chat Completions).
        # extra_body.chat_template_kwargs.enable_thinking toggles reasoning;
        # keep OFF for voice unless the server runs a reasoning parser.
        from app.services.nemotron_llm import VLLMOpenAILLMService

        return VLLMOpenAILLMService(
            api_key=settings.nemotron_llm_api_key,
            base_url=settings.nemotron_llm_url,
            settings=VLLMOpenAILLMService.Settings(
                model=settings.nemotron_llm_model,
                extra={
                    "extra_body": {
                        "chat_template_kwargs": {
                            "enable_thinking": settings.nemotron_enable_thinking
                        }
                    }
                },
            ),
        )

    if provider == "nim":
        from pipecat.services.nvidia.llm import NvidiaLLMService

        return NvidiaLLMService(
            api_key=settings.nvidia_api_key,
            model=settings.nvidia_model,
        )

    if provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        return OpenAILLMService(api_key=settings.openai_api_key, model=settings.openai_model)

    from pipecat.services.anthropic.llm import AnthropicLLMService

    return AnthropicLLMService(
        api_key=settings.anthropic_api_key,
        settings=AnthropicLLMService.Settings(model=settings.anthropic_model),
    )
