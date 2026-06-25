"""
Test fixtures.

IMPORTANT: os.environ must be populated before any app import because
app.config.Settings() is evaluated at import time.  These dummy values
ensure the test suite runs hermetically — no real .env file or secrets
required, in local dev and CI alike.
"""
import os

# ---------------------------------------------------------------------------
# Inject test defaults before any app module is imported.
# setdefault preserves values already in the environment (e.g. CI overrides)
# but guarantees the test suite never fails due to missing secrets.
# ---------------------------------------------------------------------------
_TEST_DEFAULTS: dict[str, str] = {
    "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/dentalbot_test",
    "DATABASE_URL_SYNC": "postgresql+psycopg2://user:pass@localhost:5432/dentalbot_test",
    "REDIS_URL": "redis://localhost:6379/0",
    "ANTHROPIC_API_KEY": "sk-ant-test-dummy",
    "WHATSAPP_VERIFY_TOKEN": "test-verify-token",
    "WHATSAPP_APP_SECRET": "test-app-secret",
    "WHATSAPP_ACCESS_TOKEN": "test-access-token",
    "JWT_SECRET": "test-jwt-secret-for-testing-only",
    "ENVIRONMENT": "development",
}
for _k, _v in _TEST_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

# ---------------------------------------------------------------------------
# App imports — safe after env is populated
# ---------------------------------------------------------------------------
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    session_factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
