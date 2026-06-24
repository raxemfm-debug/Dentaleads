# DentalBot

Asistente conversacional 24/7 para clínicas y consultorios dentales — SaaS multi-tenant sobre WhatsApp.

## Requisitos previos

- Docker ≥ 24 y Docker Compose v2
- [`uv`](https://docs.astral.sh/uv/) — gestor de entornos Python (instala Python 3.12 automáticamente)

### Instalar uv (una sola vez, sin sudo)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # añadir uv al PATH en esta sesión
```

## Levantar el entorno en local

### 1. Copiar las variables de entorno

```bash
cp .env.example .env
```

Edita `.env` y completa los valores reales:
- `ANTHROPIC_API_KEY` — clave de la API de Anthropic.
- `WHATSAPP_VERIFY_TOKEN` / `WHATSAPP_APP_SECRET` / `WHATSAPP_ACCESS_TOKEN` — credenciales de WhatsApp Cloud API (Meta).
- `JWT_SECRET` — cadena aleatoria larga (usa `openssl rand -hex 32`).

Para desarrollo local los valores de base de datos y Redis ya coinciden con el `docker-compose.yml`.

### 2. Levantar los servicios

```bash
docker compose up --build
```

Los servicios disponibles serán:
- API: http://localhost:8000
- Docs (Swagger): http://localhost:8000/docs
- PostgreSQL: localhost:5432
- Redis: localhost:6379

Verifica el estado:
```bash
curl http://localhost:8000/health
# {"status":"ok","database":"connected"}
```

### 3. Ejecutar migraciones

Una vez los servicios estén arriba:

```bash
docker compose exec api alembic upgrade head
```

### 4. Ejecutar los tests (sin Docker)

El proyecto usa `uv` para gestionar el entorno. Desde la raíz del repo:

```bash
# Primera vez: crear el entorno virtual con Python 3.12 e instalar deps
uv venv backend/.venv --python 3.12
uv pip install -r backend/requirements-dev.txt aiosqlite --python backend/.venv/bin/python

# Ejecutar tests (requiere las variables de entorno mínimas)
export DATABASE_URL="postgresql+asyncpg://u:p@localhost/db"
export DATABASE_URL_SYNC="postgresql+psycopg2://u:p@localhost/db"
export REDIS_URL="redis://localhost:6379/0"
export ANTHROPIC_API_KEY="test"
export WHATSAPP_VERIFY_TOKEN="test"
export WHATSAPP_APP_SECRET="test"
export WHATSAPP_ACCESS_TOKEN="test"
export JWT_SECRET="cualquier-cadena-larga-para-tests"
export ENVIRONMENT="development"

PYTHONPATH=backend backend/.venv/bin/pytest tests/ -v
```

Los tests usan SQLite in-memory y no necesitan Postgres ni Redis en ejecución.

---

## Estructura del proyecto

```
dentalbot/
├── CLAUDE.md                  # Fuente de verdad del proyecto
├── CHANGELOG.md
├── docker-compose.yml
├── .env.example
├── docs/
│   └── adr/                   # Architecture Decision Records
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── alembic.ini
│   ├── alembic/
│   │   └── versions/          # Migraciones versionadas
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── api/               # Routers FastAPI
│   │   ├── core/              # DB, auth, interfaces de proveedores
│   │   ├── models/            # Modelos SQLAlchemy
│   │   ├── schemas/           # Schemas Pydantic
│   │   ├── services/          # Lógica de negocio
│   │   └── workers/           # Tareas en segundo plano
│   └── tests/
```

## Variables de entorno

| Variable | Descripción | Requerida |
|----------|-------------|-----------|
| `DATABASE_URL` | URL async de PostgreSQL (`postgresql+asyncpg://...`) | Sí |
| `DATABASE_URL_SYNC` | URL sync de PostgreSQL para Alembic (`postgresql+psycopg2://...`) | Sí |
| `REDIS_URL` | URL de Redis | Sí |
| `ANTHROPIC_API_KEY` | Clave de la API de Anthropic | Sí |
| `WHATSAPP_VERIFY_TOKEN` | Token para verificación del webhook de WhatsApp | Sí |
| `WHATSAPP_APP_SECRET` | Secreto para validar firma `X-Hub-Signature-256` | Sí |
| `WHATSAPP_ACCESS_TOKEN` | Token de acceso a WhatsApp Cloud API | Sí |
| `JWT_SECRET` | Secreto para firma de JWT del panel de administración | Sí |
| `ENVIRONMENT` | `development` / `staging` / `production` (default: `development`) | No |
