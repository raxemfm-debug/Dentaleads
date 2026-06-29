"""Tests for the tool-calling loop (_run_llm_loop) and ToolDispatcher."""
import json

import pytest
from unittest.mock import MagicMock

from app.core.providers import LLMMessage, LLMResponse
from app.services.conversation import MAX_TOOL_ITERATIONS, _run_llm_loop
from app.services.tools import ToolContext, ToolDispatcher
from tests.fakes import SequencedFakeLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_use_response(
    tool_name: str = "verificar_disponibilidad",
    tool_id: str = "toolu_1",
) -> LLMResponse:
    return LLMResponse(
        content="Voy a verificar.",
        tool_calls=[{"id": tool_id, "name": tool_name, "inputs": {"fecha": "2026-07-01"}}],
        stop_reason="tool_use",
        usage={"input_tokens": 10, "output_tokens": 20},
    )


def _end_turn_response(content: str = "¡Listo!") -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage={"input_tokens": 5, "output_tokens": 10},
    )


def _make_ctx() -> ToolContext:
    return ToolContext(
        db=MagicMock(),
        clinic=MagicMock(),
        conv=MagicMock(),
        lead=MagicMock(),
    )


# ---------------------------------------------------------------------------
# tool_use → end_turn (normal happy path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_tool_use_then_end_turn():
    """Loop calls complete() twice: tool_use on the first, end_turn on the second."""
    async def handler(ctx, inputs):
        return json.dumps({"slots": ["10:00", "11:00"]})

    dispatcher = ToolDispatcher()
    dispatcher.register("verificar_disponibilidad", handler)

    llm = SequencedFakeLLMProvider([
        _tool_use_response(),
        _end_turn_response("¡Hay disponibilidad!"),
    ])
    messages = [LLMMessage(role="user", content="¿Tienen citas?")]

    result = await _run_llm_loop(llm, "System", messages, dispatcher, _make_ctx())

    assert result.content == "¡Hay disponibilidad!"
    assert result.stop_reason == "end_turn"
    assert len(llm.calls) == 2

    # Second call must contain the replayed assistant turn and the tool_result
    second_msgs = llm.calls[1]["messages"]
    assert any(m.role == "assistant" and m.tool_calls for m in second_msgs)
    assert any(m.role == "tool_result" for m in second_msgs)


# ---------------------------------------------------------------------------
# fallback at MAX_TOOL_ITERATIONS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_caps_at_max_iterations_and_calls_fallback_without_tools():
    """When tool_use repeats indefinitely the loop caps and forces a tools=None completion."""
    async def handler(ctx, inputs):
        return json.dumps({"ok": True})

    dispatcher = ToolDispatcher()
    dispatcher.register("verificar_disponibilidad", handler)

    # MAX_TOOL_ITERATIONS tool_use responses + 1 end_turn for the fallback call
    responses = [_tool_use_response() for _ in range(MAX_TOOL_ITERATIONS)]
    responses.append(_end_turn_response("Fallback"))
    llm = SequencedFakeLLMProvider(responses)
    messages = [LLMMessage(role="user", content="Consulta")]

    result = await _run_llm_loop(llm, "System", messages, dispatcher, _make_ctx())

    assert len(llm.calls) == MAX_TOOL_ITERATIONS + 1
    assert llm.calls[-1]["tools"] is None   # fallback call exposes no tools
    assert result.content == "Fallback"


# ---------------------------------------------------------------------------
# handler raises — error contained, loop continues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_handler_exception_becomes_error_tool_result():
    """A raising handler must not propagate; the loop injects an error tool_result and continues."""
    async def failing_handler(ctx, inputs):
        raise ValueError("DB offline")

    dispatcher = ToolDispatcher()
    dispatcher.register("verificar_disponibilidad", failing_handler)

    llm = SequencedFakeLLMProvider([
        _tool_use_response(),
        _end_turn_response("Lo siento, no pude verificar."),
    ])
    messages = [LLMMessage(role="user", content="¿Tienen citas?")]

    result = await _run_llm_loop(llm, "System", messages, dispatcher, _make_ctx())

    assert result.stop_reason == "end_turn"
    assert len(llm.calls) == 2

    second_msgs = llm.calls[1]["messages"]
    tool_results = [m for m in second_msgs if m.role == "tool_result"]
    assert len(tool_results) == 1
    payload = json.loads(tool_results[0].content)
    assert "error" in payload
    assert "DB offline" in payload["error"]


# ---------------------------------------------------------------------------
# unknown tool name — error contained, loop continues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_unknown_tool_returns_error_tool_result():
    """An unregistered tool name must produce an error tool_result without crashing."""
    llm = SequencedFakeLLMProvider([
        _tool_use_response(tool_name="herramienta_inexistente"),
        _end_turn_response("No pude ayudarte con eso."),
    ])
    dispatcher = ToolDispatcher()  # empty registry
    messages = [LLMMessage(role="user", content="Consulta")]

    result = await _run_llm_loop(llm, "System", messages, dispatcher, _make_ctx())

    assert result.stop_reason == "end_turn"
    second_msgs = llm.calls[1]["messages"]
    tool_results = [m for m in second_msgs if m.role == "tool_result"]
    assert len(tool_results) == 1
    payload = json.loads(tool_results[0].content)
    assert "error" in payload
    assert "herramienta_inexistente" in payload["error"]
