import logging

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.webhooks import router as webhook_router
from app.config import settings

# uvicorn only configures its own "uvicorn"/"uvicorn.error"/"uvicorn.access"
# loggers, not the root logger — without this, app.* logger.info() calls
# (e.g. inbound webhook logging) are silently dropped by logging's lastResort
# handler, which only surfaces WARNING and above.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="DentalBot API",
    description="Conversational assistant platform for dental clinics",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

app.include_router(health_router)
app.include_router(webhook_router)
