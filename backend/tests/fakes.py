"""
Fake provider implementations for offline testing.

These live in tests/ and are never imported by production code.
"""
from app.core.providers import (
    InboundMessage,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMTool,
    MessagingProvider,
    OutboundMessage,
)


class FakeMessagingProvider(MessagingProvider):
    """MessagingProvider that records sent messages instead of hitting the network."""

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send_message(self, message: OutboundMessage) -> str:
        self.sent.append(message)
        return "fake-wamid"

    async def send_template(self, to_number: str, template_name: str, params: list[str]) -> str:
        return "fake-wamid-template"

    def parse_inbound(self, raw_payload: dict) -> list[InboundMessage]:
        return []

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        return True


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


class SequencedFakeLLMProvider(LLMProvider):
    """Returns LLMResponse objects from a provided list in order.

    After the list is exhausted the last response repeats indefinitely.
    Records every call as a dict {system_prompt, messages, tools} for assertions.
    """

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.calls: list[dict] = []

    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append({
            "system_prompt": system_prompt,
            "messages": list(messages),
            "tools": tools,
        })
        response = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return response

    async def classify(
        self,
        text: str,
        categories: list[str],
        system_prompt: str | None = None,
    ) -> str:
        if not categories:
            raise ValueError("classify requires at least one category")
        return categories[0]


class SpyFakeLLMProvider(FakeLLMProvider):
    """
    FakeLLMProvider that records every messages list passed to complete().

    Use spy.calls[n] to assert on the context window sent for the nth completion.
    """

    def __init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []

    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        return await super().complete(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )
