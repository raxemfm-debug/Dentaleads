"""
WhatsApp Cloud API webhook — Meta verification handshake and message ingestion.

All signature verification and payload parsing is delegated to WhatsAppProvider
so that none of that logic lives in the router.
"""
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.config import settings
from app.services.whatsapp import WhatsAppProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


def _get_verify_provider() -> WhatsAppProvider:
    """Provider instance used only for signature verification and payload parsing."""
    return WhatsAppProvider(app_secret=settings.whatsapp_app_secret)


VerifyProvider = Annotated[WhatsAppProvider, Depends(_get_verify_provider)]


@router.get("/whatsapp")
async def whatsapp_challenge(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
) -> Response:
    """Meta webhook verification handshake."""
    if (
        hub_mode == "subscribe"
        and hub_verify_token == settings.whatsapp_verify_token
    ):
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_inbound(
    request: Request,
    provider: VerifyProvider,
) -> dict:
    """Receive inbound WhatsApp events; reject requests with invalid signatures."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not provider.verify_signature(body, signature):
        logger.warning("WhatsApp webhook signature verification failed")
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = json.loads(body)
    messages = provider.parse_inbound(payload)

    for msg in messages:
        logger.info(
            "inbound message tenant_phone_id=%s from=%s type=%s id=%s",
            msg.tenant_phone_id,
            msg.from_number,
            msg.message_type,
            msg.message_id,
        )
        # TODO(phase-1): route to conversation orchestrator

    return {"status": "ok"}
