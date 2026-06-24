# ADR-001: Stack tecnológico y abstracción de proveedores

**Estado:** Aceptado  
**Fecha:** 2026-06-24  
**Autores:** Equipo DentalBot

---

## Contexto

DentalBot es un SaaS multi-tenant que necesita integrar WhatsApp Cloud API y la API de Claude como dependencias externas críticas. Ambas son servicios de terceros con sus propias políticas de cambio, límites de rate, y potenciales alternativas (BSPs para WhatsApp; otros modelos LLM en el futuro). La arquitectura debe permitir sustituir cualquiera de estas sin reescribir la lógica de negocio.

## Decisión

### 1. Backend: Python 3.12 + FastAPI (async)

FastAPI con stack async completo (asyncpg, SQLAlchemy 2.0 async) por:
- Rendimiento en I/O-bound workloads (webhooks, llamadas a APIs externas).
- Type hints nativos + Pydantic v2 para validación robusta sin fricción.
- Ecosistema maduro con soporte a largo plazo.

### 2. Base de datos: PostgreSQL + SQLAlchemy 2.0 async + Alembic

- PostgreSQL por su soporte a JSONB (configuración flexible por clínica), robustez y experiencia del equipo.
- SQLAlchemy 2.0 con `mapped_column` / `Mapped[T]` (sintaxis declarativa tipada) para type safety y autocompletado.
- Alembic para migraciones versionadas; **ningún cambio de esquema sin migración**.
- Driver dual: `asyncpg` para la aplicación, `psycopg2` para Alembic (que no soporta async).

### 3. Abstracción de proveedores (`MessagingProvider` / `LLMProvider`)

**Toda llamada a WhatsApp o al LLM pasa por las interfaces abstractas definidas en `app/core/providers.py`.** La lógica de negocio solo conoce estas interfaces.

Consecuencias:
- Cambiar de WhatsApp Cloud API a un BSP (Twilio, 360dialog) = implementar `MessagingProvider`, cambiar el binding en el arranque.
- Cambiar de Claude a otro modelo = implementar `LLMProvider`, cambiar el binding.
- La lógica de agendamiento, recordatorios y captación de leads no se toca.

### 4. Motor de tareas: APScheduler en MVP → Celery + Redis

APScheduler (in-process) para los recordatorios en el MVP por simplicidad. La migración a Celery solo requiere reimplementar el módulo `workers/` sin tocar los servicios.

## Consecuencias

- **Positivo:** Máxima durabilidad ante cambios de proveedor. La lógica de negocio queda desacoplada.
- **Positivo:** Tests unitarios posibles con mocks de las interfaces sin levantar servicios externos.
- **Negativo:** Ligero overhead inicial de escribir las interfaces antes de tener implementaciones. Aceptado porque el costo de no hacerlo crece exponencialmente.
- **Riesgo:** Si las abstracciones no cubren bien los casos de uso, se crea un "leaky abstraction". Mitigación: revisar las interfaces al implementar Fase 1.
