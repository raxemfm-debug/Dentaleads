"""
WhatsApp Cloud API implementation of MessagingProvider.

Construct with only `app_secret` for webhook verification and parsing
(used before the tenant is identified). Construct with all three params
for per-tenant send operations; credentials must come from the tenant's
config, never from global settings.
"""
import hashlib
import hmac as _hmac
import json
import logging
from typing import Any

import httpx

from app.core.providers import InboundMessage, MessagingProvider, OutboundMessage

logger = logging.getLogger(__name__)

_GRAPH_API_VERSION = "v21.0"
_GRAPH_BASE = f"https://graph.facebook.com/{_GRAPH_API_VERSION}"


class WhatsAppProvider(MessagingProvider):
    """Concrete MessagingProvider backed by the WhatsApp Cloud API (Meta)."""

    def __init__(
        self,
        app_secret: str,
        phone_number_id: str = "",
        access_token: str = "",
        *,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_secret = app_secret
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._client = httpx_client  # injected in tests; created lazily otherwise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    def _require_send_credentials(self) -> None:
        if not self._phone_number_id:
            raise RuntimeError("phone_number_id is required for send operations")
        if not self._access_token:
            raise RuntimeError("access_token is required for send operations")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def _send_url(self) -> str:
        return f"{_GRAPH_BASE}/{self._phone_number_id}/messages"

    async def _post_message(self, payload: dict[str, Any]) -> str:
        self._require_send_credentials()
        client = await self._get_client()
        response = await client.post(
            self._send_url(),
            json=payload,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        data = response.json()
        return data["messages"][0]["id"]

    # ------------------------------------------------------------------
    # MessagingProvider interface
    # ------------------------------------------------------------------

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        """
        Validate the X-Hub-Signature-256 header sent by Meta.

        Meta signs the raw request body with HMAC-SHA256 using the App Secret
        and always sends the header as "sha256=<hex>".  We reject any header
        that does not carry that exact prefix so format deviations are never
        silently accepted.
        """
        _PREFIX = "sha256="
        if not isinstance(signature_header, str) or not signature_header.startswith(_PREFIX):
            return False
        provided = signature_header[len(_PREFIX):]
        expected = _hmac.new(
            self._app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return _hmac.compare_digest(expected, provided)

    def parse_inbound(self, raw_payload: dict) -> list[InboundMessage]:
        """
        Normalize a Meta webhook payload into InboundMessage objects.

        Status callbacks (delivered, read, failed) carry no 'messages' key;
        they are silently skipped so the pipeline only sees actionable events.
        """
        results: list[InboundMessage] = []
        for entry in raw_payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
                for msg in value.get("messages", []):
                    msg_type = msg.get("type", "unknown")
                    text: str | None = None
                    if msg_type == "text":
                        text = msg.get("text", {}).get("body")
                    results.append(
                        InboundMessage(
                            tenant_phone_id=phone_number_id,
                            from_number=msg.get("from", ""),
                            message_id=msg.get("id", ""),
                            text=text,
                            message_type=msg_type,
                            raw_payload=msg,
                        )
                    )
        return results

    async def send_message(self, message: OutboundMessage) -> str:
        """Send a free-form text message within the 24h session window; returns the wamid."""
        if not message.text:
            raise ValueError("OutboundMessage.text is required for send_message")
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": message.to_number,
            "type": "text",
            "text": {"body": message.text},
        }
        return await self._post_message(payload)

    async def send_template(
        self,
        to_number: str,
        template_name: str,
        params: list[str],
    ) -> str:
        """Send an approved Meta template (outside the 24h session window); returns the wamid."""
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "es"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": p} for p in params
                        ],
                    }
                ],
            },
        }
        return await self._post_message(payload)
