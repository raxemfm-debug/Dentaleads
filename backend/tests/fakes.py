"""
Fake provider implementations for offline testing.

These live in tests/ and are never imported by production code.
"""
from app.core.providers import LLMMessage, LLMProvider, LLMResponse, LLMTool


class FakeLLMProvider(LLMProvider):
    """
    Deterministic LLMProvider for tests.

    complete() returns a fixed greeting so tests can assert on the exact text
    without touching the network or the Anthropic SDK.
    classify() returns the first category, making intent-routing tests predictable.
    """

    FIXED_RESPONSE = "Hola, soy DentalBot, ¿en qué te ayudo?"

    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        return LLMResponse(
            content=self.FIXED_RESPONSE,
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
