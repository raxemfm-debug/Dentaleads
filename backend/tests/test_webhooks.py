"""
Integration tests for the WhatsApp webhook route (app.api.webhooks).

Exercises the full FastAPI route — signature verification, tenant resolution,
conversation handling — with the LLM provider and outbound WhatsApp HTTP calls
mocked so no network is touched.
"""
import hashlib
import hmac as _hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.core.providers import LLMMessage, LLMProvider, LLMProviderError, LLMResponse, LLMTool
from app.main import app
from app.models.clinic import Clinic
from app.models.message import Message
from app.services.llm import get_llm_provider
from tests.fakes import FakeLLMProvider

# WHATSAPP_APP_SECRET test default set in tests/conftest.py before app import.
_APP_SECRET = "test-app-secret"
_PHONE_NUMBER_ID = "PHONE_NUMBER_ID_WEBHOOK_TEST"


def _sign(body: bytes) -> str:
    digest = _hmac.new(_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _text_payload(text: str, from_number: str = "15559990000") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "BUSINESS_ACCOUNT_ID",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": _PHONE_NUMBER_ID},
                    "messages": [{
                        "from": from_number,
                        "id": "wamid.inbound-err-001",
                        "timestamp": "1700000000",
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
                "field": "messages",
            }],
        }],
    }


class _FailingLLMProvider(LLMProvider):
    """Simulates ClaudeProvider after it has translated an Anthropic SDK outage."""

    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        raise LLMProviderError("simulated Anthropic outage")

    async def classify(self, text: str, categories: list[str], system_prompt: str | None = None) -> str:
        raise LLMProviderError("simulated Anthropic outage")


@pytest.fixture
def mock_whatsapp_send():
    """Patch the httpx client used by WhatsAppProvider so send_message never hits the network."""
    with patch("app.services.whatsapp.httpx.AsyncClient") as MockCls:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"messages": [{"id": "wamid-fallback"}]}

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        MockCls.return_value = mock_client
        yield mock_client


@pytest.fixture
def failing_llm_provider():
    app.dependency_overrides[get_llm_provider] = lambda: _FailingLLMProvider()
    yield
    app.dependency_overrides.pop(get_llm_provider, None)


@pytest.fixture
def fake_llm_provider():
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider()
    yield
    app.dependency_overrides.pop(get_llm_provider, None)


@pytest.fixture
def failing_whatsapp_send():
    """Simulate a 401 from Meta when sending — e.g. an expired access token (DT-004)."""
    with patch("app.services.whatsapp.httpx.AsyncClient") as MockCls:
        request = httpx.Request("POST", "https://graph.facebook.com/fake")
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=request, response=MagicMock(status_code=401)
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        MockCls.return_value = mock_client
        yield mock_client


async def test_llm_failure_returns_200_and_sends_fallback(
    client, db_session, mock_whatsapp_send, failing_llm_provider
):
    clinic = Clinic(name="Clínica Webhook Test", whatsapp_phone_id=_PHONE_NUMBER_ID, config={})
    db_session.add(clinic)
    await db_session.flush()

    body = json.dumps(_text_payload("Hola, ¿tienen turno mañana?")).encode()
    response = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    mock_whatsapp_send.post.assert_awaited_once()
    sent_json = mock_whatsapp_send.post.call_args.kwargs["json"]
    assert "problema técnico" in sent_json["text"]["body"]


# ---------------------------------------------------------------------------
# Send failure (DT-004, piece 1) — must return 200 and must not duplicate messages
# ---------------------------------------------------------------------------

async def test_send_failure_returns_200_and_does_not_duplicate_messages(
    client, db_session, failing_whatsapp_send, fake_llm_provider
):
    clinic = Clinic(name="Clínica Send Fail Test", whatsapp_phone_id=_PHONE_NUMBER_ID, config={})
    db_session.add(clinic)
    await db_session.flush()

    body = json.dumps(_text_payload("Hola, necesito una cita", from_number="15557778888")).encode()
    response = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    msgs = (await db_session.execute(select(Message))).scalars().all()
    assert len(msgs) == 2
