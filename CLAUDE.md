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

- **clinics** (tenant): `id`, `name`, `whatsapp_phone_id`, `timezone`, `address`, `address_reference`, `maps_url`, `contact_phone`, `business_hours` (JSONB), `config` (JSONB), `subscription_status`, `created_at`.
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
- **Ubicación y contacto de la clínica**: el system prompt incluye una "Ficha de la clínica" (dirección, referencia, link de Google Maps, teléfono de contacto) inyectada junto a la fecha/hora actual del tenant (`_build_system_prompt` / `_format_ficha_clinica` en `backend/app/services/conversation.py`). Los datos viven en columnas dedicadas de `clinics` (`address`, `address_reference`, `maps_url`, `contact_phone`), no en `config` JSONB, por ser hechos fijos de la clínica y no ajustes de tono/comportamiento. Si a un tenant le falta algún dato, el bot lo admite honestamente y ofrece derivar a un humano — nunca inventa una dirección, link o teléfono. Tests: `backend/tests/test_conversation.py` (ficha por tenant correcto, aislamiento entre tenants, caso sin datos configurados).
- **Horario de atención en la ficha de la clínica**: la misma "Ficha de la clínica" también incluye el horario, humanizado en español y agrupando días consecutivos con el mismo horario (ej. "Lun-Vie 9:00 a.m.-1:00 p.m. y 3:00 p.m.-7:00 p.m., Sáb 9:00 a.m.-1:00 p.m., Dom Cerrado"), vía `_format_horario_atencion` en `backend/app/services/conversation.py`. Se renderiza desde `clinics.business_hours` (JSONB), el mismo dato que ya usa `AvailabilityService` para `verificar_disponibilidad`/`agendar_cita` — no es una fuente nueva. Hallazgo en vivo (2026-07-10): "¿atienden los domingos?" caía al fallback honesto de "no tengo ese dato" aunque `business_hours` ya estaba configurado; el system prompt ahora instruye responder preguntas generales de horario directamente desde la ficha, sin usar herramientas, reservando `verificar_disponibilidad` para confirmar turnos libres en una fecha específica. Sin `business_hours` configurado, el campo se omite y aplica el mismo fallback honesto de la ficha (nunca inventa un horario). Tests: `backend/tests/test_conversation.py` (agrupación simple, franja partida por almuerzo + día cerrado con la forma real de producción, omisión sin `business_hours`, caso end-to-end vía `handle()`).
- **Datos ya conocidos del paciente**: el system prompt también inyecta una "Ficha del paciente" (nombre, tratamiento de interés) resuelta desde `leads` para el número de WhatsApp del remitente (`_build_system_prompt` / `_format_ficha_paciente` en `backend/app/services/conversation.py`, llamada desde `handle()`). Existe porque el guion de agendamiento del bot pedía el nombre incluso cuando ya estaba en `leads.name`, ignorando el historial — el dato sobrevive aunque salga de la ventana de `HISTORY_LIMIT`. El system prompt instruye explícitamente no volver a pedir un dato que ya figura en la ficha. Tests: `backend/tests/test_conversation.py` (ficha con lead conocido, sin datos para lead nuevo, ausente cuando no se pasa `lead`, y casos end-to-end vía `handle()`).
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
- **Validación de antigüedad del mensaje**: antes de procesar, el handler del webhook (`app/api/webhooks.py`) compara `messages[].timestamp` (epoch en segundos del payload de Meta, poblado en `InboundMessage.timestamp` por `WhatsAppProvider.parse_inbound`) contra la hora actual, vía `is_message_stale()` en `app/services/conversation.py`. El umbral es configurable por `WEBHOOK_MAX_MESSAGE_AGE_SECONDS` (default 900s = 15 min). Un mensaje más viejo que el umbral no se procesa (ni LLM ni persistencia), se loggea con `logger.warning` (wamid + antigüedad) y aun así responde 200 — mismo principio que la idempotencia por wamid de DT-004: es un redelivery huérfano de Meta, no un turno vivo del paciente, y un 4xx/5xx solo lograría que Meta siga reintentando. Fail-open si el timestamp falta o es inválido (`<= 0`, el default de `InboundMessage.timestamp` cuando el payload no lo trae): tratar una antigüedad desconocida como "vieja" arriesgaría descartar un mensaje real de un paciente, que es peor que reprocesar un redelivery raro. Tests: `backend/tests/test_webhooks.py` (mensaje fresco se procesa, mensaje de 2h se descarta sin persistir ni llamar al LLM, mensaje justo bajo el umbral se procesa, timestamp=0 se procesa por fail-open).

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
| DT-001 | `app/services/conversation.py` (paso 8) | Los intercambios intermedios del loop de tool-calling (turnos `tool_use` + `tool_results`) no se persistían; solo se guardaba la respuesta final de texto. | Dificultaba la depuración de conversaciones fallidas y la auditoría del comportamiento del bot en producción. Hallazgo en vivo (2026-07-05): tras agendar con éxito (`agendar_cita` → `status: "scheduled"`), un simple "gracias" del paciente hizo que el modelo volviera a invocar `agendar_cita` con el mismo `fecha_hora`; como el turno `tool_use`/`tool_result` original no quedó en el historial persistido, el modelo no tenía forma de recordar que esa cita ya estaba confirmada, y el guard de doble-booking (`uq_appointments_tenant_slot`) devolvió `slot_no_disponible` sobre el propio slot del paciente, confundiéndolo. Cerrada en dos piezas el 2026-07-07: pieza 1 (commit `76e2f89`) persiste los turnos intermedios `tool_use`/`tool_result` con `metadata_` de inputs/outputs, dando al modelo memoria de sus tool calls previas; pieza 2 (commit `7e4c92c`) añade al system prompt, junto a la fecha/hora inyectada por tenant, la instrucción de nunca volver a agendar una cita ya confirmada en la misma conversación y de no invocar herramientas para mensajes puramente sociales (agradecimientos, despedidas, "ok"/"gracias"). Verificado en vivo el 2026-07-07: se repitió el escenario ("gracias" tras confirmación) y el modelo respondió solo con texto, sin volver a invocar `agendar_cita`. | **Cerrada** (2026-07-07) |
| DT-002 | `backend/Dockerfile` / imagen `api` | La imagen Docker de `api` quedó desactualizada respecto a `requirements.txt`: `anthropic` se agregó al manifiesto el 2026-06-27 pero la imagen no se reconstruyó, así que el contenedor entraba en crash-loop con `ModuleNotFoundError: No module named 'anthropic'`. No fue un problema de manifiesto — `requirements.txt` ya estaba correcto — sino de una imagen sin rebuild. | Bloqueaba cualquier prueba end-to-end, incluida la integración real con WhatsApp Cloud API. Detectado y resuelto el 2026-07-02 con `docker compose up -d --build api`. Recordatorio: tras tocar `requirements.txt` hace falta `--build`, un `restart` o `up -d` sin `--build` no alcanza. | **Resuelta** (2026-07-02) |
| DT-003 | `app/services/conversation.py` `handle()` | La llamada al SDK de Anthropic (vía `ClaudeProvider`) no está envuelta en try/except. Un error del proveedor (rate limit, auth, timeout) se propaga sin capturar hasta el router del webhook (`app/api/webhooks.py`), que responde 500. | Meta interpreta el 500 como fallo de entrega: reintenta el webhook y, si se repite lo suficiente, puede marcar la suscripción como no saludable o desactivarla. Resuelto el 2026-07-03: `ClaudeProvider.complete()` (`app/services/llm.py`) traduce `anthropic.APIError` y subclases (`APITimeoutError`, `APIConnectionError`, `APIStatusError`, ...) a un `LLMProviderError` genérico definido en `app/core/providers.py`, preservando la abstracción de proveedor. `handle()` captura `LLMProviderError`, hace `logger.error(..., exc_info=True)`, persiste el intercambio (mensaje del paciente + fallback) y responde por WhatsApp con "Disculpa, tuve un problema técnico. ¿Puedes intentar de nuevo en un momento?" sin relanzar la excepción, así el webhook siempre devuelve 200. Tests: `tests/test_claude_provider.py::TestCompleteErrorHandling`, `tests/test_conversation.py` (casos LLM provider error) y `tests/test_webhooks.py` (200 end-to-end). | **Resuelta** (2026-07-03) |
| DT-004 | `app/services/conversation.py` `handle()` (paso 9) / `app/services/whatsapp.py` `_post_message` | Detectado en producción el 2026-07-03 durante la prueba end-to-end real: el `WHATSAPP_ACCESS_TOKEN` configurado es un token temporal de usuario (obtenido del panel "API Setup" de Meta, asociado a una cuenta personal, no un system user de larga duración) y expiró ~1h después de emitido. `WhatsAppProvider._post_message` llama `response.raise_for_status()`, que lanzó `httpx.HTTPStatusError: 401 Unauthorized` al intentar enviar la respuesta. A diferencia de la llamada al LLM (DT-003, ya resuelta), la llamada a `messaging.send_message()` en el paso 9 de `handle()` no está envuelta en try/except, así que la excepción se propagó sin capturar hasta el router → 500 a Meta. | Dos efectos observados en los logs reales: (1) Meta reintentó el mismo webhook (mismo `wamid`) al menos 5 veces en pocos minutos, cada vez re-ejecutando `handle()` completo y fallando igual mientras el token siga inválido; (2) como no hay chequeo de idempotencia por `message_id` antes de procesar, cada reintento persistió un nuevo par de filas `messages` (user+assistant) para el mismo `wamid` — la conversación queda con duplicados en la DB, y una vez el token se renueve, el bot mandaría la respuesta duplicada al paciente tantas veces como reintentos haya recibido. Resuelto en dos piezas el 2026-07-04. Pieza 1 (blindaje del envío): se añade `MessagingProviderError` (`app/core/providers.py`), análogo a `LLMProviderError` de DT-003. `WhatsAppProvider._post_message` traduce `httpx.HTTPError` (cubre tanto errores de transporte como respuestas no-2xx vía `raise_for_status`) a esa excepción genérica, sin filtrar `httpx` hacia la lógica de negocio. `handle()` envuelve el envío en un helper `_send_reply` que captura `MessagingProviderError`, hace `logger.error(..., exc_info=True)` y no relanza — el intercambio ya quedó persistido antes del envío, así que un fallo de entrega nunca vuelve a convertirse en un 500 hacia Meta. Pieza 2 (idempotencia por `wamid`): se añade una columna `wamid` a `messages` (nullable, unique index `uq_messages_wamid` — mismo patrón que el guard de doble-booking de `appointments`, sin necesidad de partial index porque NULL nunca colisiona consigo mismo) en vez de una tabla `processed_messages` aparte, ya que el wamid vive lógicamente en el mensaje entrante. Solo las filas `role="user"` llevan `wamid`; las de `role="assistant"` quedan NULL. Migración Alembic `c919bd395589`. `app.services.conversation.is_wamid_processed()` consulta esa columna; el router del webhook la llama antes de invocar `handle()` por cada mensaje entrante y, si ya fue procesado, responde 200 sin reprocesar. Tests: `tests/test_whatsapp_provider.py` (traducción de errores de envío), `tests/test_conversation.py` (fallo de envío no propaga / persiste igual, idempotencia por wamid) y `tests/test_webhooks.py` (200 sin duplicar en fallo de envío, mismo wamid dos veces → un solo par + un solo envío). Pendiente por separado: pasar a un token de system user de larga duración en vez del token temporal de usuario (no es un defecto de código, es una tarea operativa de configuración en Meta). | **Resuelta** (2026-07-04) |
| DT-006 | `app/services/conversation.py` `_get_or_create_conversation` / `app/models/message.py` | Hallazgo en vivo con evidencia en DB (2026-07-10): dos webhooks concurrentes de la misma conversación (dos mensajes reales del paciente, ~5s aparte, no un reintento de Meta del mismo wamid — eso ya lo cubre DT-004) se procesaron en paralelo y ambos asignaron los mismos `sequence` (72 y 73 duplicados en la conversación `cb32ffe3-437d-492f-97bc-d21c35793d36`). Causa: `sequence` se calculaba en Python como `max()+1` sin serialización, y el índice `(conversation_id, sequence)` no era único, así que la DB no vetaba el empate. | Historial de la conversación con dos secuencias 72/73 duplicadas — riesgo de romper la reconstrucción del historial (`_rows_to_llm_messages` espera `sequence` como orden total) y de que las dos respuestas del bot llegaran entrelazadas/fuera de orden al paciente. Resuelto el 2026-07-10: (1) `_get_or_create_conversation` agrega `.with_for_update()` al SELECT de la conversación existente — el lock se mantiene hasta el commit de la request (una sola transacción por webhook vía `get_db()`), así que un segundo webhook para la misma conversación bloquea hasta que el primero termine su turno completo (LLM + tools + persistencia + envío WhatsApp), lo que garantiza `sequence` sin colisión y además serializa el procesamiento por conversación (ningún entrelazado de respuestas). Verificado portable: `with_for_update()` compila a lock real en Postgres y a no-op inofensivo en el motor SQLite de los tests. (2) `Index("ix_messages_conversation_id_sequence", ...)` pasa a `unique=True` (`uq_messages_conversation_id_sequence`, mismo patrón que `uq_messages_wamid`/`uq_appointments_tenant_slot`) como backstop a nivel DB. Migración Alembic `a9b593a43546`: primero repara los duplicados existentes (renumera solo las conversaciones afectadas vía `ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY created_at, sequence, id)`, mismo enfoque que el backfill original `7cbaca3d97ec`) y en la misma migración crea el índice único — si la reparación dejara algo sin resolver, `CREATE UNIQUE INDEX` falla ahí mismo en vez de dejar un arreglo parcial silencioso. Aplicada a la DB de dev el 2026-07-10: sin duplicados restantes, 80 filas antes y después (nada se perdió, solo se renumeró), conversación `cb32ffe3` con 72/73 sin tocar y el segundo par corrido a 74/75 preservando el orden cronológico real. Tests: `tests/test_message_sequence_race_postgres.py` (nuevo, gateado por `TEST_POSTGRES_URL`, mismo patrón que `test_appointment_race_postgres.py`). Nota de metodología: la primera versión del test pasaba incluso con el lock deshabilitado — un LLM fake sin latencia dejaba que el primer racer completara todo su turno (incluido el commit) antes de que el segundo empezara a leer el historial, así que nunca competían de verdad. Se agregó un `asyncio.sleep(0.15)` en el fake LLM para simular la latencia real de la API de Anthropic (~1-3s, la ventana que causó el incidente real), y se confirmó manualmente ambos sentidos: falla 3/3 con el lock deshabilitado (`IntegrityError` en `uq_messages_conversation_id_sequence`), pasa 5/5 con el lock restaurado. Deuda pendiente, no resuelta aquí: `_get_or_create_lead` tiene la misma race read-then-maybe-insert para números nunca vistos (sin `with_for_update` ni unique constraint en `(tenant_id, whatsapp_number)`) — candidato a DT-007 si se repite en vivo. | **Resuelta** (2026-07-10) |

**Nota de testabilidad (2026-07-07):** `AvailabilityService.get_available_slots` (`app/services/availability.py`) ahora acepta un parámetro opcional `today: date | None = None` — mismo patrón que `now` en `_handle_agendar_cita` (`app/services/tools.py`) y `reference_date` en `compute_slots`: por defecto usa el reloj real, pero los tests pueden congelar "hoy" para que las fechas hardcodeadas en las pruebas no queden obsoletas cuando el reloj real las supere. Se agregó al arreglar 5 tests de `TestAgendarCita` en `tests/test_tool_handlers.py` que fallaban con `fecha_pasada`/alternativas vacías por depender del reloj real. Al escribir nuevos tests para lógica que depende de la fecha/hora actual, preferir inyectar `now`/`today`/`reference_date` en vez de fechas absolutas sin override.

---

*Fin de CLAUDE.md — mantener este documento actualizado a medida que el producto evoluciona.*

    

