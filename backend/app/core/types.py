"""
Cross-database type wrappers.

On PostgreSQL (production): render as native JSONB / UUID.
On other dialects (SQLite in tests): fall back to JSON / VARCHAR(36).

Business logic imports these types from here — never directly from
sqlalchemy.dialects.postgresql — so the test suite needs no real Postgres.
"""
import uuid as _uuid

from sqlalchemy import JSON, String, types
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.dialects.postgresql import UUID as _PG_UUID


class JSONB(types.TypeDecorator):
    """JSONB on PostgreSQL; JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PG_JSONB())
        return dialect.type_descriptor(JSON())


class UUID(types.TypeDecorator):
    """UUID on PostgreSQL; VARCHAR(36) elsewhere."""

    impl = String
    cache_ok = True

    def __init__(self, as_uuid: bool = True, **kwargs):
        self.as_uuid = as_uuid
        kwargs.pop("length", None)  # Alembic autogenerate may pass length=36 redundantly
        super().__init__(36, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PG_UUID(as_uuid=self.as_uuid))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if self.as_uuid and not isinstance(value, _uuid.UUID):
            return _uuid.UUID(str(value))
        return value
