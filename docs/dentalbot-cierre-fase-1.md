# DentalBot — Cierre de Fase 1 (2026-07-05)

## El hito

**Cita real agendada por conversación natural de WhatsApp, de punta a punta:**
Meta Cloud API → túnel cloudflared → webhook (firma X-Hub-Signature-256) → resolución de tenant → Claude + tools → Postgres → respuesta en el móvil.

Evidencia verificada en DB:

- `appointments`: id `20770758-a1bb-4d55-87eb-ddb79f99f065`, tenant Clínica Dental Demo (`0c560080-...`), `scheduled_at = 2026-07-17 20:30+00` (= viernes 17/07 **15:30 America/Lima**), status `confirmed`, tratamiento Ortodoncia vinculado.
- `leads`: Jhoseph R. Wayne, whatsapp `51940569811`, interested_treatment = Ortodoncia.

## Los tres guards, probados en producción sin buscarlo

1. **Doble-reserva** (`uq_appointments_tenant_slot`): un "gracias" del paciente hizo que el modelo re-invocara `agendar_cita` sobre el mismo slot → IntegrityError capturado con SAVEPOINT → `slot_no_disponible` + alternativas. El índice defendió la fila.
2. **Blindaje de envío** (DT-004 pieza 1): token temporal expiró en vivo → 401 de graph.facebook.com → `MessagingProviderError` logueada, cero 500 a Meta, intercambio persistido.
3. **Idempotencia por wamid** (DT-004 pieza 2): retries de Meta no duplicaron mensajes ni respuestas.

## Deuda y hallazgos abiertos

- **DT-001** (única DT de código abierta): tool calls no persistidos. Hallazgo en vivo documentado en CLAUDE.md — el "gracias" re-disparó `agendar_cita` porque el modelo no ve estructuralmente sus tool calls previos. Fix propuesto: persistir turnos `tool_use`/`tool_result` + línea en system prompt ("nunca re-agendes una cita ya confirmada en esta conversación").
- **Pendiente menor**: documentar por qué la cita quedó `confirmed` y no `scheduled` (¿default de columna o decisión del handler?).
- **Operativo, no código**: token de system user en Meta (Business Settings → Usuarios del sistema → token con whatsapp_business_messaging + whatsapp_business_management, sin expiración) — elimina el regenerar cada hora.
- **DT-005 resuelta hoy**: fecha actual (tz del tenant) inyectada en system prompt; el bot dejó de alucinar fechas 2025. Confirmada en vivo.

## Aclaración de diseño (pregunta del día)

Las zonas horarias multi-tenant **ya funcionan**: cada clínica tiene `timezone` en su fila (`clinics.timezone`, ZoneInfo). Una clínica en Madrid = `Europe/Madrid` y disponibilidad/validaciones/`scheduled_at` operan en su zona. No hay trabajo pendiente aquí.

## Recordatorios operativos (siguen vigentes en dev)

- Túnel nuevo = URL nueva = re-editar callback en Meta + Verificar y guardar. Siempre.
- cloudflared con `--protocol http2` (QUIC falla en esta red).
- `--reload` recarga código, NO variables de entorno: tras tocar `.env` → `docker compose up -d --force-recreate api` (y reenganchar el monitor de logs: cambia el container ID).
- Tras tocar `requirements.txt` → `--build`.
- `.env` real en la raíz: `~/dentalbot/.env`. Secretos: editar con nano + clic derecho, nunca pegarlos en chats.
- Certificado de cliente en el webhook de Meta: APAGADO.

## Fase 2 — arranque propuesto

1. Token de system user (primero: desbloquea todo lo demás).
2. DT-001 (con el caso real como test de regresión).
3. Recordatorios 24h/2h (requiere scheduler — evaluar arq/celery beat vs cron simple).
4. Hosting real (adiós quick tunnel): túnel con nombre o VPS/PaaS.
