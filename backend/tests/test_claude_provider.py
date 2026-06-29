"""
Isolated tests for ClaudeProvider.

All Anthropic SDK calls are mocked — no network, no real API key required.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.providers import LLMMessage, LLMTool
from app.services.llm import ClaudeProvider, _CLASSIFY_MODEL, _COMPLETE_MODEL, get_llm_provider


# ---------------------------------------------------------------------------
# Helpers to build fake Anthropic response objects
# ---------------------------------------------------------------------------

def _text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _tool_use_block(id: str, name: str, input: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = id
    block.name = name
    block.input = input
    return block


def _make_response(
    *,
    content_blocks: list,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    resp = MagicMock()
    resp.content = content_blocks
    resp.stop_reason = stop_reason
    resp.usage = usage
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """Patch AsyncAnthropic at the import site in app.services.llm."""
    with patch("app.services.llm.AsyncAnthropic") as MockCls:
        client = MagicMock()
        client.messages.create = AsyncMock()
        MockCls.return_value = client
        yield client


@pytest.fixture
def provider(mock_client) -> ClaudeProvider:
    return ClaudeProvider(api_key="sk-ant-test")


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------

class TestComplete:
    @pytest.mark.asyncio
    async def test_text_response(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("Hola, ¿en qué te ayudo?")]
        )
        result = await provider.complete(
            system_prompt="Eres un asistente dental.",
            messages=[LLMMessage(role="user", content="Hola")],
        )
        assert result.content == "Hola, ¿en qué te ayudo?"
        assert result.tool_calls == []
        assert result.stop_reason == "end_turn"
        assert result.usage == {"input_tokens": 10, "output_tokens": 20}

    @pytest.mark.asyncio
    async def test_uses_complete_model(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("OK")]
        )
        await provider.complete(
            system_prompt="S",
            messages=[LLMMessage(role="user", content="X")],
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == _COMPLETE_MODEL

    @pytest.mark.asyncio
    async def test_system_prompt_is_top_level_param(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("OK")]
        )
        await provider.complete(
            system_prompt="Eres un asistente dental.",
            messages=[LLMMessage(role="user", content="Hola")],
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "Eres un asistente dental."
        for msg in call_kwargs["messages"]:
            assert msg.get("role") != "system"

    @pytest.mark.asyncio
    async def test_tool_use_response(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[
                _text_block("Voy a verificar."),
                _tool_use_block("toolu_1", "verificar_disponibilidad", {"fecha": "2026-07-01"}),
            ],
            stop_reason="tool_use",
        )
        result = await provider.complete(
            system_prompt="S",
            messages=[LLMMessage(role="user", content="¿Tienen citas?")],
            tools=[LLMTool(
                name="verificar_disponibilidad",
                description="Verifica disponibilidad.",
                input_schema={"type": "object", "properties": {"fecha": {"type": "string"}}},
            )],
        )
        assert result.content == "Voy a verificar."
        assert result.stop_reason == "tool_use"
        assert result.tool_calls == [{
            "id": "toolu_1",
            "name": "verificar_disponibilidad",
            "inputs": {"fecha": "2026-07-01"},
        }]

    @pytest.mark.asyncio
    async def test_tools_converted_to_anthropic_format(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("OK")]
        )
        tool = LLMTool(
            name="agendar_cita",
            description="Agenda una cita.",
            input_schema={"type": "object", "properties": {"fecha": {"type": "string"}}},
        )
        await provider.complete(
            system_prompt="S",
            messages=[LLMMessage(role="user", content="X")],
            tools=[tool],
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["tools"] == [{
            "name": "agendar_cita",
            "description": "Agenda una cita.",
            "input_schema": {"type": "object", "properties": {"fecha": {"type": "string"}}},
        }]

    @pytest.mark.asyncio
    async def test_no_tools_omits_tools_kwarg(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("OK")]
        )
        await provider.complete(
            system_prompt="S",
            messages=[LLMMessage(role="user", content="X")],
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "tools" not in call_kwargs

    @pytest.mark.asyncio
    async def test_tool_result_message_converted(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("OK")]
        )
        messages = [
            LLMMessage(role="user", content="Quiero una cita"),
            LLMMessage(role="assistant", content="Voy a verificar."),
            LLMMessage(
                role="tool_result",
                content="Disponible el 01/07/2026 a las 10:00",
                tool_call_id="toolu_1",
            ),
        ]
        await provider.complete(system_prompt="S", messages=messages)
        sent = mock_client.messages.create.call_args.kwargs["messages"]
        assert sent[0] == {"role": "user", "content": "Quiero una cita"}
        assert sent[1] == {"role": "assistant", "content": "Voy a verificar."}
        assert sent[2] == {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": "Disponible el 01/07/2026 a las 10:00",
            }],
        }

    @pytest.mark.asyncio
    async def test_assistant_with_tool_calls_serialized_as_content_array(self, provider, mock_client):
        """An assistant turn with tool_calls must be sent as a content-block array, not a plain string."""
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("OK")]
        )
        messages = [
            LLMMessage(role="user", content="¿Tienen citas mañana?"),
            LLMMessage(
                role="assistant",
                content="Voy a verificar.",
                tool_calls=[{
                    "id": "toolu_1",
                    "name": "verificar_disponibilidad",
                    "inputs": {"fecha": "2026-07-01"},
                }],
            ),
            LLMMessage(
                role="tool_result",
                content='{"slots": ["10:00"]}',
                tool_call_id="toolu_1",
            ),
        ]
        await provider.complete(system_prompt="S", messages=messages)
        sent = mock_client.messages.create.call_args.kwargs["messages"]
        assert sent[1] == {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Voy a verificar."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "verificar_disponibilidad",
                    "input": {"fecha": "2026-07-01"},
                },
            ],
        }

    @pytest.mark.asyncio
    async def test_multi_text_blocks_concatenated(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("Hola "), _text_block("mundo")],
        )
        result = await provider.complete(
            system_prompt="S",
            messages=[LLMMessage(role="user", content="Hi")],
        )
        assert result.content == "Hola mundo"

    @pytest.mark.asyncio
    async def test_max_tokens_forwarded(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("OK")]
        )
        await provider.complete(
            system_prompt="S",
            messages=[LLMMessage(role="user", content="X")],
            max_tokens=512,
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 512


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

class TestClassify:
    @pytest.mark.asyncio
    async def test_returns_matching_category(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("agendar_cita")]
        )
        result = await provider.classify(
            "Quiero una cita para el martes",
            categories=["agendar_cita", "consulta_precio", "urgencia"],
        )
        assert result == "agendar_cita"

    @pytest.mark.asyncio
    async def test_uses_haiku_model(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("agendar_cita")]
        )
        await provider.classify("Quiero cita", categories=["agendar_cita"])
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == _CLASSIFY_MODEL

    @pytest.mark.asyncio
    async def test_fallback_to_first_category_on_no_match(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("respuesta_desconocida")]
        )
        result = await provider.classify(
            "Texto sin categoría clara",
            categories=["agendar_cita", "consulta_precio"],
        )
        assert result == "agendar_cita"

    @pytest.mark.asyncio
    async def test_raises_on_empty_categories(self, provider, mock_client):
        with pytest.raises(ValueError, match="classify requires at least one category"):
            await provider.classify("Texto", categories=[])

    @pytest.mark.asyncio
    async def test_custom_system_prompt_forwarded(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("urgencia")]
        )
        await provider.classify(
            "Me duele mucho",
            categories=["urgencia", "rutina"],
            system_prompt="Clasifica la urgencia.",
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "Clasifica la urgencia."

    @pytest.mark.asyncio
    async def test_default_system_prompt_lists_categories(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("agendar_cita")]
        )
        await provider.classify(
            "Quiero cita",
            categories=["agendar_cita", "consulta_precio"],
        )
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "agendar_cita" in call_kwargs["system"]
        assert "consulta_precio" in call_kwargs["system"]

    @pytest.mark.asyncio
    async def test_category_match_is_case_insensitive(self, provider, mock_client):
        mock_client.messages.create.return_value = _make_response(
            content_blocks=[_text_block("CONSULTA_PRECIO")]
        )
        result = await provider.classify(
            "¿Cuánto cuesta?",
            categories=["agendar_cita", "consulta_precio"],
        )
        assert result == "consulta_precio"


# ---------------------------------------------------------------------------
# get_llm_provider()
# ---------------------------------------------------------------------------

class TestGetLLMProvider:
    def test_returns_claude_provider_instance(self):
        provider = get_llm_provider()
        assert isinstance(provider, ClaudeProvider)
