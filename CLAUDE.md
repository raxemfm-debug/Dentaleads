    # CLAUDE.md — DentalBot: Asistente Conversacional para Clínicas y Consultorios Dentales

> Este archivo es la **fuente de verdad** del proyecto. Claude Code debe leerlo al inicio de cada sesión y respetar sus convenciones, arquitectura y fases. Colócalo en la raíz del repositorio.

---

## 1. Visión del producto

**DentalBot** es una plataforma SaaS multi-clínica que provee un asistente conversacional inteligente (potenciado por la API de Claude) conectado a **WhatsApp** como canal principal, orientado exclusivamente a **clínicas y consultorios dentales** en LATAM y España.

No es un clon completo de ManyChat. Es un producto **vertical y especializado**: hace pocas cosas, pero las hace excepcionalmente bien para el nicho dental, donde el dolor real del cliente es:

1. **Inasistencias a citas** (no-shows) que cuestan dinero.
2. **Respuestas lentas** a consultas por WhatsApp fuera de horario.
3. **Captación y calificación de leads** que se pierden por demora.
4. **Agendamiento manual** que consume tiempo del personal de recepción.

### Propuesta de valor (one-liner)
> "Tu recepcionista digital 24/7 en WhatsApp: agenda citas, responde dudas de tratamientos y reduce inasistencias con recordatorios automáticos."

---

## 2. Mercado objetivo y modelo de negocio

- **Cliente final**: clínicas y consultorios dentales pequeños y medianos (1–10 sillones) en LATAM y España.
- **Idioma**: español (variantes neutras LATAM y de España).
- **Modelo**: SaaS por suscripción mensual, **multi-tenant** (cada clínica = un tenant aislado con su propia configuración, número de WhatsApp, tratamientos, horarios y datos).
- **Distribución**: venta directa y plataformas como Hotmart (cuando se ofrezca como producto/servicio empaquetado).

> ⚠️ **Multi-tenancy es un requisito de diseño desde el día 1.** Nunca asumir una sola clínica. Todos los datos y configuraciones deben estar particionados por `tenant_id` (clínica).

---

## 3. Alcance: MVP vs. Futuro

### MVP (Fase 1 y 2 — construir primero)
- Conexión a WhatsApp Cloud API (recepción y envío de mensajes).
- Motor conversacional con Claude (FAQ, calificación, intención).
- Agendamiento de citas con verificación de disponibilidad.
- Recordatorios automáticos de citas (24h y 2h antes).
- Captación de leads (nombre, motivo, tratamiento de interés, contacto).
- Derivación a humano (human handoff) cuando el bot no puede resolver.
- Panel de administración básico por clínica.

### Fuera del MVP (Fases posteriores)
- Constructor visual de flujos drag-and-drop (estilo ManyChat).
- Campañas de difusión / broadcast masivo.
- Integración con Instagram DM y Facebook Messenger.
- Integración con software dental / historia clínica.
- Métricas avanzadas y reportes.
- Pagos / señas de reserva en línea.

> No construir funcionalidades de fases posteriores hasta que el MVP esté completo, probado y desplegado. Resistir la tentación de sobre-ingeniería.

---

## 4. Arquitectura técnica

```
┌─────────────┐     webhook      ┌──────────────────────────┐
│  WhatsApp   │ ───────────────► │   API Backend (FastAPI)  │
│  Cloud API  │ ◄─────────────── │  - Webhook handler       │
└─────────────┘   envío msgs     │  - Orquestador conversac.│
                                  │  - Motor de citas        │
┌─────────────┐                  │  - Gestión de leads      │
│  Panel Web  │ ◄──── REST ────► │  - Multi-tenancy         │
│  (React)    │                  └───────────┬──────────────┘
└─────────────┘                              │
                          ┌──────────────────┼──────────────────┐
                          ▼                  ▼                  ▼
                   ┌────────────┐    ┌──────────────┐   ┌──────────────┐
                   │ PostgreSQL │    │  Claude API  │   │ Cola/Worker  │
                   │ (datos)    │    │ (conversac.) │   │ (recordator.)│
                   └────────────┘    └──────────────┘   └──────────────┘
```

### Flujo conversacional (alto nivel)
1. Llega un mensaje de WhatsApp → webhook de FastAPI lo recibe.
2. Se identifica el `tenant` por el número de WhatsApp de destino.
3. Se carga el contexto de la conversación (historial, datos del lead, config de la clínica).
4. El **orquestador** llama a Claude con el system prompt de la clínica + herramientas (tools/function calling): `verificar_disponibilidad`, `agendar_cita`, `guardar_lead`, `derivar_a_humano`.
5. Claude responde y/o ejecuta herramientas; el resultado se envía de vuelta por WhatsApp.
6. Se persiste el estado de la conversación.

---

## 5. Stack tecnológico

| Capa | Tecnología | Justificación |
|------|-----------|---------------|
| Backend / API | **Python 3.12 + FastAPI** | Async, rápido, ideal para webhooks; alineado con tu aprendizaje. |
| Base de datos | **PostgreSQL** | Relacional, robusto, soporta multi-tenancy y JSONB. |
| ORM | **SQLAlchemy 2.0 + Alembic** | Estándar de la industria; migraciones versionadas. |
| Validación | **Pydantic v2** | Validación de schemas y settings. |
| Motor conversacional | **Claude API** (`claude-sonnet-4-6` para conversación; `claude-haiku-4-5` para clasificación barata) | Balance calidad/costo/latencia. |
| Mensajería | **WhatsApp Cloud API** (Meta) | Canal principal del nicho en LATAM/España. |
| Tareas en segundo plano | **Celery + Redis** (o APScheduler en MVP) | Recordatorios programados. |
| Frontend (panel) | **React + Vite + Tailwind** | Reutiliza tu experiencia previa en React. |
| Auth panel | **JWT + bcrypt** | Autenticación estándar del panel admin. |
| Despliegue | **Docker + docker-compose** | Reproducible; fácil de mover a VPS/Railway/Render. |
| Calendario (MVP) | **Google Calendar API** o tabla interna de slots | Acelera el agendamiento inicial. |

> No introducir nuevas dependencias sin justificarlas. Mantener el stack mínimo y coherente.

---

## 6. Modelo de datos (entidades núcleo)

Todas las tablas de negocio incluyen `tenant_id` (FK a `clinics`) salvo `clinics` y `users` globales.

- **clinics** (tenant): `id`, `name`, `whatsapp_phone_id`, `timezone`, `address`, `business_hours` (JSONB), `config` (JSONB), `subscription_status`, `created_at`.
- **clinic_users**: `id`, `tenant_id`, `email`, `password_hash`, `role` (`owner`/`staff`), `name`.
- **treatments**: `id`, `tenant_id`, `name`, `description`, `duration_minutes`, `price_from`, `requires_consult`.
- **leads**: `id`, `tenant_id`, `whatsapp_number`, `name`, `interested_treatment_id`, `source`, `status` (`new`/`qualified`/`scheduled`/`lost`), `notes`, `consent_at`, `created_at`.
- **conversations**: `id`, `tenant_id`, `lead_id`, `channel`, `status` (`bot`/`human`/`closed`), `last_message_at`.
- **messages**: `id`, `conversation_id`, `role` (`user`/`assistant`/`system`), `content`, `metadata` (JSONB), `created_at`.
- **appointments**: `id`, `tenant_id`, `lead_id`, `treatment_id`, `scheduled_at`, `status` (`confirmed`/`reminded`/`completed`/`cancelled`/`no_show`), `reminder_24h_sent`, `reminder_2h_sent`.

> **Datos de salud:** la información puede ser sensible. Minimizar lo que se almacena, registrar consentimiento (`consent_at`) y nunca guardar diagnósticos clínicos. Ver sección 11.

---

## 7. Funcionalidades dentales específicas

El bot debe manejar de forma nativa el dominio dental. Configurable por clínica, pero con plantillas por defecto:

- **Tratamientos frecuentes**: limpieza/profilaxis, ortodoncia (brackets/alineadores), implantes, blanqueamiento, endodoncia, extracciones, prótesis, odontopediatría.
- **Preguntas típicas a resolver**: precios "desde", duración del tratamiento, si requiere valoración previa, formas de pago/financiamiento, horarios, ubicación, si atienden urgencias.
- **Triaje de urgencias**: detectar palabras clave (dolor intenso, golpe, sangrado, hinchazón) y priorizar/derivar a humano con mensaje empático.
- **Agendamiento**: ofrecer franjas disponibles, confirmar, registrar la cita.
- **Recordatorios**: 24h y 2h antes, con opción de confirmar/cancelar respondiendo. Reduce no-shows (principal ROI para la clínica).
- **Tono**: cálido, profesional, tranquilizador (la odontología genera ansiedad). Nunca dar diagnósticos ni consejos médicos; siempre derivar la valoración al profesional.

---

## 8. Integraciones — especificaciones

### 8.1 WhatsApp Cloud API
- Verificación de webhook (GET con `hub.challenge`).
- Recepción de mensajes (POST) con verificación de firma `X-Hub-Signature-256`.
- Identificación de tenant por `phone_number_id` del payload.
- Envío de mensajes de sesión (texto libre dentro de ventana de 24h) y **plantillas (templates)** aprobadas para recordatorios fuera de ventana.
- Manejar tipos: texto, botones interactivos (para confirmar citas), listas.

### 8.2 Claude API (motor conversacional)
- Usar **function calling / tools** para acciones, no parsear texto libre.
- Tools mínimas: `verificar_disponibilidad(fecha)`, `agendar_cita(...)`, `guardar_lead(...)`, `consultar_tratamiento(nombre)`, `derivar_a_humano(motivo)`.
- El system prompt se compone dinámicamente con la config de la clínica (nombre, tratamientos, horarios, tono).
- Incluir el historial reciente de la conversación en cada llamada (Claude no tiene memoria entre requests).
- Manejar errores y rate limits con reintentos exponenciales.

### 8.3 Recordatorios (worker)
- Job periódico que busca citas próximas sin recordatorio enviado y dispara plantillas de WhatsApp.
- Idempotente: usar flags `reminder_24h_sent` / `reminder_2h_sent` para no duplicar.

---

## 9. Estructura del proyecto

```
dentalbot/
├── CLAUDE.md                  # este archivo
├── docker-compose.yml
├── .env.example               # nunca commitear .env real
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py          # settings con Pydantic
│   │   ├── api/               # routers (webhooks, panel)
│   │   ├── core/              # auth, multi-tenancy, seguridad
│   │   ├── models/            # SQLAlchemy
│   │   ├── schemas/           # Pydantic
│   │   ├── services/
│   │   │   ├── whatsapp.py
│   │   │   ├── claude_engine.py
│   │   │   ├── scheduling.py
│   │   │   └── reminders.py
│   │   └── workers/           # Celery/APScheduler
│   ├── alembic/               # migraciones
│   └── tests/
└── frontend/                  # panel React (fase 2)
```

---

## 10. Roadmap por fases

**Fase 0 — Cimientos**
Estructura del repo, docker-compose (Postgres+Redis), config, modelos base, migración inicial, esqueleto de FastAPI con healthcheck.

**Fase 1 — Núcleo conversacional**
Webhook de WhatsApp (verificación + recepción), identificación de tenant, motor de Claude con tools, persistencia de conversaciones, FAQ y captación de leads. Probar con un solo tenant de prueba.

**Fase 2 — Agendamiento y recordatorios**
Modelo de citas, verificación de disponibilidad, agendar/cancelar vía botones, worker de recordatorios 24h/2h, human handoff.

**Fase 3 — Panel de administración**
Login, dashboard de conversaciones y leads, configuración de la clínica (tratamientos, horarios, tono), vista de citas.

**Fase 4 — Multi-tenant productivo + onboarding**
Alta de nuevas clínicas, aislamiento verificado, gestión de suscripciones, despliegue.

**Fases posteriores**: constructor visual de flujos, broadcast, Instagram/Messenger, métricas avanzadas, pagos.

> Cada fase debe quedar **funcional y probada** antes de pasar a la siguiente.

---

## 11. Seguridad, privacidad y cumplimiento

- **Datos sensibles**: minimizar; no almacenar diagnósticos. Registrar consentimiento del paciente antes de guardar datos personales.
- **Cumplimiento**: considerar GDPR (España) y leyes locales de protección de datos (p. ej. Ley N.º 29733 en Perú, LGPD en Brasil, etc.). Permitir borrado de datos a solicitud.
- **Secretos**: tokens de WhatsApp, claves de Claude y credenciales de BD van en variables de entorno, nunca en el código ni en el repo.
- **Webhooks**: validar siempre la firma de WhatsApp.
- **Aislamiento de tenants**: toda consulta filtra por `tenant_id`. Escribir tests que verifiquen que un tenant no accede a datos de otro.
- **Disclaimer médico**: el bot nunca diagnostica ni reemplaza al profesional; deja constancia de ello en sus respuestas cuando corresponda.

---

## 12. Instrucciones de trabajo para Claude Code

1. **Lee este archivo antes de cada sesión** y trabaja por fases; no saltes fases.
2. **Pregunta antes de asumir** decisiones de producto no definidas aquí (p. ej. proveedor de calendario, política de cancelación).
3. **Multi-tenancy siempre**: ninguna funcionalidad debe asumir una sola clínica.
4. **Tipado y validación**: usa type hints, Pydantic y docstrings. Código en inglés; mensajes al usuario final del bot en español.
5. **Tests**: escribe pruebas para la lógica de negocio (agendamiento, aislamiento de tenants, idempotencia de recordatorios) antes de dar una fase por terminada.
6. **Migraciones**: todo cambio de modelo requiere una migración Alembic.
7. **Commits pequeños y descriptivos**; explica decisiones de arquitectura no triviales.
8. **No sobre-ingenierizar**: la simplicidad es una característica. Si algo no está en el MVP, no lo construyas todavía.
9. **Secretos fuera del código**: usa `.env` y documenta variables en `.env.example`.
10. **Antes de cerrar una fase**, ejecuta los tests y deja un breve resumen de lo hecho y lo pendiente.

---

## 13. Variables de entorno requeridas (`.env.example`)

```
DATABASE_URL=postgresql://user:pass@localhost:5432/dentalbot
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_ACCESS_TOKEN=
JWT_SECRET=
ENVIRONMENT=development
```

---

## 14. Principios de longevidad y evolución

Este proyecto está diseñado para **durar y crecer indefinidamente**: el código se actualizará y ampliará de forma continua a medida que lleguen ventas y nuevas necesidades. Para que eso sea sostenible, todo el equipo (humano o IA) respeta estos principios:

1. **Capas de abstracción para proveedores externos.** WhatsApp y el modelo de lenguaje (Claude) se acceden **siempre detrás de una interfaz** (`MessagingProvider`, `LLMProvider`), nunca llamando a sus SDKs directamente desde la lógica de negocio. Objetivo: poder cambiar de Cloud API a un BSP (360dialog/Twilio), o de un modelo a otro, tocando una sola clase y no todo el sistema. Esta es la decisión más importante para la durabilidad.

2. **Configuración sobre código.** El comportamiento por clínica (tratamientos, horarios, tono, mensajes) vive en **datos/configuración**, no hardcodeado. Ampliar el alcance o sumar una clínica casi nunca debe requerir tocar el núcleo. Si una nueva necesidad obliga a modificar código central, es señal de que falta un punto de extensión.

3. **Fronteras modulares claras.** Cada servicio (`whatsapp`, `claude_engine`, `scheduling`, `reminders`) tiene una responsabilidad única y se comunica por interfaces explícitas. Nada de lógica de negocio dentro de los routers ni de los modelos.

4. **Tests como red de seguridad para refactorizar.** La cobertura no es burocracia: es lo que permite cambiar código sin miedo dentro de un año. Priorizar tests en lógica crítica (agendamiento, aislamiento de tenants, idempotencia de recordatorios).

5. **Documentación viva y decisiones registradas.** Mantener `CLAUDE.md` actualizado y registrar decisiones de arquitectura no triviales en `docs/adr/` (Architecture Decision Records breves: contexto, decisión, consecuencias). Quien retome el proyecto en el futuro debe entender *por qué* se hizo algo, no solo *qué*.

6. **Versionado y trazabilidad.** Usar versionado semántico y mantener un `CHANGELOG.md`. Toda evolución del esquema de datos pasa por una migración Alembic; **nunca** cambios destructivos sin respaldo y migración hacia adelante.

7. **Compatibilidad hacia atrás por defecto.** Al ampliar APIs o modelos, preferir cambios aditivos. Romper compatibilidad es una decisión consciente que se documenta.

8. **Deuda técnica visible.** Cuando se tome un atajo deliberado, marcarlo con `# TODO(deuda):` y una breve nota. La deuda oculta es la que mata proyectos a largo plazo.

> Regla de oro: antes de añadir una funcionalidad, preguntar *"¿esto hará el sistema más fácil o más difícil de cambiar dentro de un año?"*. Si la respuesta es "más difícil", buscar una abstracción.

---

## 15. Deuda técnica conocida

Atajos deliberados marcados con `# TODO(deuda):` en el código. Registrar aquí para visibilidad entre sesiones.

| ID | Archivo | Descripción | Impacto | Estado |
|----|---------|-------------|---------|--------|
| DT-001 | `app/services/conversation.py` (paso 8) | Los intercambios intermedios del loop de tool-calling (turnos `tool_use` + `tool_results`) no se persisten; solo se guarda la respuesta final de texto. | Dificulta la depuración de conversaciones fallidas y la auditoría del comportamiento del bot en producción. Post-MVP: persistir el rastro completo con `role="tool_use"/"tool_result"` y `metadata_` con inputs/outputs. | Abierta |
| DT-002 | `backend/Dockerfile` / imagen `api` | La imagen Docker de `api` quedó desactualizada respecto a `requirements.txt`: `anthropic` se agregó al manifiesto el 2026-06-27 pero la imagen no se reconstruyó, así que el contenedor entraba en crash-loop con `ModuleNotFoundError: No module named 'anthropic'`. No fue un problema de manifiesto — `requirements.txt` ya estaba correcto — sino de una imagen sin rebuild. | Bloqueaba cualquier prueba end-to-end, incluida la integración real con WhatsApp Cloud API. Detectado y resuelto el 2026-07-02 con `docker compose up -d --build api`. Recordatorio: tras tocar `requirements.txt` hace falta `--build`, un `restart` o `up -d` sin `--build` no alcanza. | **Resuelta** (2026-07-02) |
| DT-003 | `app/services/conversation.py` `handle()` | La llamada al SDK de Anthropic (vía `ClaudeProvider`) no está envuelta en try/except. Un error del proveedor (rate limit, auth, timeout) se propaga sin capturar hasta el router del webhook (`app/api/webhooks.py`), que responde 500. | Meta interpreta el 500 como fallo de entrega: reintenta el webhook y, si se repite lo suficiente, puede marcar la suscripción como no saludable o desactivarla. Resuelto el 2026-07-03: `ClaudeProvider.complete()` (`app/services/llm.py`) traduce `anthropic.APIError` y subclases (`APITimeoutError`, `APIConnectionError`, `APIStatusError`, ...) a un `LLMProviderError` genérico definido en `app/core/providers.py`, preservando la abstracción de proveedor. `handle()` captura `LLMProviderError`, hace `logger.error(..., exc_info=True)`, persiste el intercambio (mensaje del paciente + fallback) y responde por WhatsApp con "Disculpa, tuve un problema técnico. ¿Puedes intentar de nuevo en un momento?" sin relanzar la excepción, así el webhook siempre devuelve 200. Tests: `tests/test_claude_provider.py::TestCompleteErrorHandling`, `tests/test_conversation.py` (casos LLM provider error) y `tests/test_webhooks.py` (200 end-to-end). | **Resuelta** (2026-07-03) |

---

*Fin de CLAUDE.md — mantener este documento actualizado a medida que el producto evoluciona.*

    

