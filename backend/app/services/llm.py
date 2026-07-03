"""
LLM service layer — ClaudeProvider wraps the Anthropic SDK behind LLMProvider.

The provider interface keeps business logic decoupled from the SDK so the
model or vendor can be swapped by changing this file alone.
"""
from anthropic import APIError, AsyncAnthropic

from app.config import settings
from app.core.providers import LLMMessage, LLMProvider, LLMProviderError, LLMResponse, LLMTool

_COMPLETE_MODEL = "claude-sonnet-4-6"
_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"


def _to_anthropic_messages(messages: list[LLMMessage]) -> list[dict]:
    result = []
    for msg in messages:
        if msg.role == "tool_result":
            result.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": msg.content,
                }],
            })
        elif msg.role == "assistant" and msg.tool_calls:
            # Anthropic requires a content array when the assistant turn contains tool_use
            # blocks. Plain-string content is not valid in this case.
            content_blocks: list[dict] = []
            if msg.content:
                content_blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["inputs"],
                })
            result.append({"role": "assistant", "content": content_blocks})
        else:
            result.append({"role": msg.role, "content": msg.content})
    return result


def _to_anthropic_tools(tools: list[LLMTool]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


class ClaudeProvider(LLMProvider):
    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": _COMPLETE_MODEL,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": _to_anthropic_messages(messages),
        }
        if tools:
            kwargs["tools"] = _to_anthropic_tools(tools)

        try:
            response = await self._client.messages.create(**kwargs)
        except APIError as exc:
            # Covers the whole Anthropic SDK error hierarchy (APITimeoutError,
            # APIConnectionError, APIStatusError and its subclasses like
            # RateLimitError/AuthenticationError all inherit from APIError).
            raise LLMProviderError(f"Anthropic API error: {exc}") from exc

        content_text = ""
        tool_calls: list[dict] = []
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "inputs": block.input,
                })

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    async def classify(
        self,
        text: str,
        categories: list[str],
        system_prompt: str | None = None,
    ) -> str:
        if not categories:
            raise ValueError("classify requires at least one category")

        categories_list = ", ".join(f'"{c}"' for c in categories)
        system = system_prompt or (
            f"Classify the following text into exactly one of these categories: {categories_list}. "
            "Respond with only the category name, no explanation."
        )
        response = await self._client.messages.create(
            model=_CLASSIFY_MODEL,
            max_tokens=64,
            system=system,
            messages=[{"role": "user", "content": text}],
        )

        raw = response.content[0].text.strip()
        for cat in categories:
            if cat.lower() in raw.lower():
                return cat
        return categories[0]


def get_llm_provider() -> LLMProvider:
    return ClaudeProvider(api_key=settings.anthropic_api_key)
