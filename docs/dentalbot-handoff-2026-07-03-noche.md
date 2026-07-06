# DentalBot — Handoff para la noche del 03/07

## LO LOGRADO HOY (histórico)

**El bot está VIVO**: primera conversación real por WhatsApp completada de punta a punta (Meta → túnel → webhook → firma → tenant → Claude → tools → respuesta en el móvil). Saludó como Clínica Dental Demo, pidió nombre/tratamiento y captó "el viernes".

Además: DT-003 resuelta y commiteada (c7552bc), key de Anthropic rotada con $5 de saldo, .env completo con valores reales (raíz: ~/dentalbot/.env), y app Dentaleads suscrita a la WABA.

## BUG ABIERTO (retomar aquí)

**CAUSA RAÍZ DIAGNOSTICADA** (Claude Code, 12:44): httpx.HTTPStatusError 401 de graph.facebook.com — el token temporal de usuario expiró (~1-2h de vida). Dos bugs reales destapados:
1. `messaging.send_message()` en el paso 9 de handle() NO está envuelto en try/except (a diferencia del LLM, ya blindado en DT-003) → el 401 propaga → 500 a Meta → Meta reintenta el webhook completo (~5 veces mismo wamid en logs).
2. Sin idempotencia por message_id: cada reintento re-corre handle() completo y persiste un nuevo par user+assistant en messages para el mismo wamid → duplicados en DB y, con token sano, respuestas duplicadas al paciente.

Se eligió "registrar como deuda" (DT-004) para cerrar la sesión. PLAN PARA LA NOCHE, en orden:
1. Fix: envolver send_message() análogo a DT-003 (log + no propagar 500) + idempotencia por wamid (chequeo de message_id procesado ANTES de handle(); tabla o Redis). Tests: mismo wamid dos veces → una sola respuesta y un solo par en messages; fallo de envío → no 500.
2. Token NUEVO de Meta (el actual está vencido; considerar token de system user, larga duración, para dejar de sufrir esto) → .env → recrear api.
3. Re-probar contra Meta el embudo completo hasta agendar cita y ver la fila en appointments.

## Para relevantar el entorno por la noche

1. Docker: `docker compose up -d` + healthcheck.
2. Túnel (se mató al salir): `cloudflared tunnel --url http://localhost:8000 --protocol http2` → URL NUEVA.
3. Meta → Webhooks (objeto WhatsApp Business Account) → Editar: pegar URL nueva + /webhook/whatsapp, token dev-verify-token → Verificar y guardar. **Dejar APAGADO el toggle de certificado de cliente.**
4. Verificar campo messages = Suscritos.
5. Token de Meta: si pasaron >24h desde ~10:45am, regenerar y actualizar .env + recrear api.

## Aprendizajes de config Meta (tribal, ya documentado en sesión)

- App debe estar suscrita a la WABA: `POST /{waba_id}/subscribed_apps` (el panel genérico de Webhooks NO lo hace).
- El sample del panel llega por otro camino — no prueba la suscripción WABA.
- Quick tunnel de cloudflared: sin garantía de uptime; QUIC falla en esta red → usar `--protocol http2`. Producción: túnel con nombre o hosting real.
- Túnel nuevo = URL nueva = re-editar callback en Meta.

## Pendientes tras el bug

- DT-001 (rastro de tool calls) y DT-004 (idempotencia, arriba).
- Probar el embudo completo hasta agendar cita real y ver la fila en appointments.
- Detalle: el bot dio ejemplo de fecha "2025-07-18" (estamos en 2026) → inyectar fecha actual (tz del tenant) en el system prompt. Candidato DT-005.
