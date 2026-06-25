"""
Unit tests for WhatsAppProvider.

All tests are offline — no network calls, no database.
httpx is mocked via constructor injection for send tests.
"""
import hashlib
import hmac as _hmac
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.providers import OutboundMessage
from app.services.whatsapp import WhatsAppProvider

# ------------------------------------------------------------------
# Shared constants
# ------------------------------------------------------------------

_APP_SECRET = "test_app_secret_abc"
_PHONE_NUMBER_ID = "999000111"
_ACCESS_TOKEN = "EAAtest_access_token"
_WAMID = "wamid.HBgLMTU1NTk5OTAwMDAVAgARGBI"


def _make_provider(*, with_send_credentials: bool = True, httpx_client=None) -> WhatsAppProvider:
    return WhatsAppProvider(
        app_secret=_APP_SECRET,
        phone_number_id=_PHONE_NUMBER_ID if with_send_credentials else "",
        access_token=_ACCESS_TOKEN if with_send_credentials else "",
        httpx_client=httpx_client,
    )


def _sign(body: bytes) -> str:
    """Compute the expected X-Hub-Signature-256 header value."""
    digest = _hmac.new(_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _mock_httpx_client(wamid: str = _WAMID) -> MagicMock:
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"messages": [{"id": wamid}]}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

TEXT_PAYLOAD: dict = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "BUSINESS_ACCOUNT_ID",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550001111",
                            "phone_number_id": "PHONE_NUMBER_ID_123",
                        },
                        "contacts": [
                            {"profile": {"name": "Paciente Test"}, "wa_id": "15559990000"}
                        ],
                        "messages": [
                            {
                                "from": "15559990000",
                                "id": "wamid.inbound001",
                                "timestamp": "1700000000",
                                "type": "text",
                                "text": {"body": "Hola, ¿cuánto cuesta la limpieza?"},
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}

STATUS_PAYLOAD: dict = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "BUSINESS_ACCOUNT_ID",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550001111",
                            "phone_number_id": "PHONE_NUMBER_ID_123",
                        },
                        "statuses": [
                            {
                                "id": "wamid.sent001",
                                "status": "delivered",
                                "timestamp": "1700000001",
                                "recipient_id": "15559990000",
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}

IMAGE_PAYLOAD: dict = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "BUSINESS_ACCOUNT_ID",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550001111",
                            "phone_number_id": "PHONE_NUMBER_ID_123",
                        },
                        "messages": [
                            {
                                "from": "15559990000",
                                "id": "wamid.image001",
                                "timestamp": "1700000002",
                                "type": "image",
                                "image": {"id": "img_001", "mime_type": "image/jpeg"},
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}


# ------------------------------------------------------------------
# parse_inbound tests
# ------------------------------------------------------------------


def test_parse_inbound_text_message():
    provider = _make_provider()
    msgs = provider.parse_inbound(TEXT_PAYLOAD)

    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.tenant_phone_id == "PHONE_NUMBER_ID_123"
    assert msg.from_number == "15559990000"
    assert msg.message_id == "wamid.inbound001"
    assert msg.message_type == "text"
    assert msg.text == "Hola, ¿cuánto cuesta la limpieza?"
    assert msg.raw_payload["type"] == "text"


def test_parse_inbound_status_callback_returns_empty_list():
    """Status notifications (delivered/read/failed) must be silently ignored."""
    provider = _make_provider()
    msgs = provider.parse_inbound(STATUS_PAYLOAD)
    assert msgs == []


def test_parse_inbound_non_text_message_has_no_text():
    """Non-text message types are parsed but text is set to None."""
    provider = _make_provider()
    msgs = provider.parse_inbound(IMAGE_PAYLOAD)

    assert len(msgs) == 1
    assert msgs[0].message_type == "image"
    assert msgs[0].text is None


def test_parse_inbound_empty_payload():
    provider = _make_provider()
    assert provider.parse_inbound({}) == []


def test_parse_inbound_multiple_messages():
    """Multiple messages in a single webhook call are all parsed."""
    payload: dict = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "ACCOUNT_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "PID"},
                            "messages": [
                                {
                                    "from": "111",
                                    "id": "wamid.a",
                                    "timestamp": "1",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                },
                                {
                                    "from": "222",
                                    "id": "wamid.b",
                                    "timestamp": "2",
                                    "type": "text",
                                    "text": {"body": "¿Atienden urgencias?"},
                                },
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    provider = _make_provider()
    msgs = provider.parse_inbound(payload)
    assert len(msgs) == 2
    assert msgs[0].from_number == "111"
    assert msgs[1].from_number == "222"


# ------------------------------------------------------------------
# verify_signature tests
# ------------------------------------------------------------------


def test_verify_signature_valid():
    provider = _make_provider()
    body = b'{"test":"payload"}'
    assert provider.verify_signature(body, _sign(body)) is True


def test_verify_signature_invalid_wrong_secret():
    provider = WhatsAppProvider(app_secret="wrong_secret")
    body = b'{"test":"payload"}'
    assert provider.verify_signature(body, _sign(body)) is False


def test_verify_signature_tampered_body():
    provider = _make_provider()
    original_body = b'{"test":"original"}'
    tampered_body = b'{"test":"tampered"}'
    assert provider.verify_signature(tampered_body, _sign(original_body)) is False


def test_verify_signature_missing_prefix():
    """Header without sha256= prefix must fail gracefully (not crash)."""
    provider = _make_provider()
    body = b'{"test":"payload"}'
    # provide raw hex without the "sha256=" prefix
    raw_hex = _hmac.new(_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert provider.verify_signature(body, raw_hex) is False


def test_verify_signature_empty_header():
    provider = _make_provider()
    assert provider.verify_signature(b"body", "") is False


# ------------------------------------------------------------------
# send_message tests
# ------------------------------------------------------------------


async def test_send_message_returns_wamid():
    mock_client = _mock_httpx_client()
    provider = _make_provider(httpx_client=mock_client)

    result = await provider.send_message(
        OutboundMessage(to_number="+15559990000", text="Su cita está confirmada.")
    )

    assert result == _WAMID
    mock_client.post.assert_awaited_once()


async def test_send_message_posts_correct_payload():
    mock_client = _mock_httpx_client()
    provider = _make_provider(httpx_client=mock_client)

    await provider.send_message(
        OutboundMessage(to_number="+15559990000", text="Hola desde DentalBot")
    )

    _, kwargs = mock_client.post.call_args
    sent_json = kwargs["json"]
    assert sent_json["messaging_product"] == "whatsapp"
    assert sent_json["to"] == "+15559990000"
    assert sent_json["type"] == "text"
    assert sent_json["text"]["body"] == "Hola desde DentalBot"


async def test_send_message_includes_auth_header():
    mock_client = _mock_httpx_client()
    provider = _make_provider(httpx_client=mock_client)

    await provider.send_message(OutboundMessage(to_number="+111", text="test"))

    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"]["Authorization"] == f"Bearer {_ACCESS_TOKEN}"


async def test_send_message_raises_without_credentials():
    provider = _make_provider(with_send_credentials=False)
    with pytest.raises(RuntimeError, match="phone_number_id"):
        await provider.send_message(OutboundMessage(to_number="+111", text="test"))


# ------------------------------------------------------------------
# send_template tests
# ------------------------------------------------------------------


async def test_send_template_returns_wamid():
    mock_client = _mock_httpx_client()
    provider = _make_provider(httpx_client=mock_client)

    result = await provider.send_template(
        to_number="+15559990000",
        template_name="recordatorio_cita",
        params=["María", "mañana a las 10:00"],
    )

    assert result == _WAMID
    mock_client.post.assert_awaited_once()


async def test_send_template_posts_correct_payload():
    mock_client = _mock_httpx_client()
    provider = _make_provider(httpx_client=mock_client)

    await provider.send_template(
        to_number="+15559990000",
        template_name="recordatorio_cita",
        params=["María", "mañana a las 10:00"],
    )

    _, kwargs = mock_client.post.call_args
    sent_json = kwargs["json"]
    assert sent_json["messaging_product"] == "whatsapp"
    assert sent_json["to"] == "+15559990000"
    assert sent_json["type"] == "template"
    template = sent_json["template"]
    assert template["name"] == "recordatorio_cita"
    assert template["language"]["code"] == "es"
    params_sent = template["components"][0]["parameters"]
    assert params_sent[0] == {"type": "text", "text": "María"}
    assert params_sent[1] == {"type": "text", "text": "mañana a las 10:00"}


async def test_send_template_empty_params():
    mock_client = _mock_httpx_client()
    provider = _make_provider(httpx_client=mock_client)

    await provider.send_template(
        to_number="+111",
        template_name="bienvenida",
        params=[],
    )

    _, kwargs = mock_client.post.call_args
    components = kwargs["json"]["template"]["components"]
    assert components[0]["parameters"] == []
