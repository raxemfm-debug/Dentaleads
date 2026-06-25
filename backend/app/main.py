from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.webhooks import router as webhook_router
from app.config import settings

app = FastAPI(
    title="DentalBot API",
    description="Conversational assistant platform for dental clinics",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

app.include_router(health_router)
app.include_router(webhook_router)
