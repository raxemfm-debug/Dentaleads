# Changelog

Todas las entradas siguen [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).
El versionado sigue [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-06-24

### Fase 0: Cimientos

#### Añadido
- Estructura de directorios del proyecto (`backend/`, `docs/`, `alembic/`, `tests/`).
- `docker-compose.yml` con servicios PostgreSQL 16 y Redis 7.
- `backend/Dockerfile` con Python 3.12 slim.
- `.env.example` con todas las variables de entorno requeridas.
- `config.py`: configuración tipada con Pydantic Settings; falla si falta variable obligatoria.
- `core/database.py`: motor async SQLAlchemy 2.0 + `get_db` como dependencia FastAPI.
- **Modelos SQLAlchemy** (todos con `tenant_id` indexado, salvo `clinics`):
  - `clinics` — tabla maestra de tenants.
  - `clinic_users` — usuarios del panel por clínica.
  - `treatments` — catálogo de tratamientos dentales por clínica.
  - `leads` — pacientes potenciales con consentimiento y estado.
  - `conversations` — sesiones conversacionales.
  - `messages` — historial de mensajes con metadatos JSONB.
  - `appointments` — citas con flags de recordatorio idempotentes.
- `alembic/` inicializado con `env.py` que lee `DATABASE_URL_SYNC` del entorno.
- `app/main.py`: aplicación FastAPI con `GET /health` que verifica conexión a BD.
- `core/providers.py`: interfaces abstractas `MessagingProvider` y `LLMProvider` con dataclasses para mensajes entrantes/salientes, tool calling y clasificación.
- `tests/conftest.py`: fixtures con SQLite in-memory para tests sin Postgres.
- `tests/test_health.py`: test del endpoint `/health`.
- `tests/test_multitenant.py`: tests de aislamiento por `tenant_id` en modelo `Lead`.
- `docs/adr/001-stack-and-provider-abstraction.md`: ADR con decisiones de stack.
- `README.md` con instrucciones para levantar el entorno.

[0.1.0]: https://github.com/tu-org/dentalbot/releases/tag/v0.1.0
