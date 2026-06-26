"""
LLM service layer.

ClaudeProvider will live here. Until it is implemented, get_llm_provider()
returns _PlaceholderLLMProvider — a deterministic stand-in that lets the
webhook pipeline run end-to-end in development without touching the network.
"""
from app.core.providers import LLMMessage, LLMProvider, LLMResponse, LLMTool


class _PlaceholderLLMProvider(LLMProvider):
    # TODO(deuda): delete once ClaudeProvider is implemented
    _REPLY = (
        "Hola, soy el asistente de la clínica. En este momento estoy en configuración; "
        "por favor contacta directamente con nosotros."
    )

    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        return LLMResponse(
            content=self._REPLY,
            tool_calls=[],
            stop_reason="end_turn",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    async def classify(
        self,
        text: str,
        categories: list[str],
        system_prompt: str | None = None,
    ) -> str:
        if not categories:
            raise ValueError("classify requires at least one category")
        return categories[0]


def get_llm_provider() -> LLMProvider:
    # TODO(deuda): return ClaudeProvider(settings.anthropic_api_key) once implemented
    return _PlaceholderLLMProvider()
