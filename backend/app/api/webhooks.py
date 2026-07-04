"""
WhatsApp Cloud API webhook — Meta verification handshake and message ingestion.

All signature verification and payload parsing is delegated to WhatsAppProvider
so that none of that logic lives in the router.
"""
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.exceptions import TenantNotFoundError
from app.core.providers import LLMProvider
from app.services.conversation import handle, is_wamid_processed
from app.services.llm import get_llm_provider
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
    db: AsyncSession = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
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
        if await is_wamid_processed(db, msg.message_id):
            logger.info("duplicate wamid=%s — already processed, skipping", msg.message_id)
            continue

        send_provider = WhatsAppProvider(
            app_secret=settings.whatsapp_app_secret,
            phone_number_id=msg.tenant_phone_id,
            access_token=settings.whatsapp_access_token,
        )
        try:
            await handle(msg=msg, db=db, messaging=send_provider, llm=llm)
        except TenantNotFoundError:
            logger.warning("unknown tenant phone_id=%s — skipping", msg.tenant_phone_id)

    return {"status": "ok"}
