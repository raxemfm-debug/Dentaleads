# DentalBot — Handoff para la noche del 04/07: re-prueba Meta

## Estado (todo commiteado y pusheado, suite 129 verdes + 1 skip)

- `f3f9966` DT-004 pieza 1: MessagingProviderError en frontera de WhatsAppProvider; fallo de envío → log + 200, nunca 500 a Meta.
- `d14c9fa` DT-004 pieza 2: idempotencia por wamid — columna en messages + índice único uq_messages_wamid + gate en el router antes de handle(). Migración `c919bd395589`.
- `7f5e121` CLAUDE.md: DT-004 Resuelta. Queda abierta (operativa, no código): token de system user.

## Secuencia de la noche, en orden

1. **Docker + migración** (¡la que nos mordió una vez!): `docker compose up -d` → aplicar `alembic upgrade head` con el patrón de env vars contra localhost:5432 → healthcheck.
2. **Token**: preferible system user (business.facebook.com → Configuración del negocio → Usuarios del sistema → crear (admin) → asignar app Dentaleads + activo WhatsApp → generar token con permisos whatsapp_business_messaging y whatsapp_business_management — NO expira en horas). Alternativa rápida: token temporal de API Setup (24h). → `.env` (raíz: ~/dentalbot/.env, editar con nano + clic derecho para pegar) → `docker compose up -d --force-recreate api`.
3. **Túnel**: `cloudflared tunnel --url http://localhost:8000 --protocol http2` (http2, no QUIC) → copiar URL nueva.
4. **Meta**: Webhooks → objeto WhatsApp Business Account → Editar: URL nueva + `/webhook/whatsapp`, token `dev-verify-token`, certificado de cliente APAGADO → Verificar y guardar → campo `messages` Suscritos. (La suscripción a la WABA persiste, no re-hacer.)
5. **La conversación completa**: "hola" → nombre → ortodoncia → fecha/hora → cita agendada → pedir a Claude Code la fila en `appointments`. Si el bot re-pregunta fecha con ejemplo "2025-...", recordar DT-005 (inyectar fecha actual en system prompt) — anotar, no arreglar en caliente.

## Verificaciones extra que esta prueba regala

- Reenviar un mensaje ya procesado (Meta retry simulado) → el bot NO debe duplicar respuesta (idempotencia en vivo).
- Si el envío fallara por token → log del error, paciente sin duplicados, Meta sin 500 (pieza 1 en vivo).

## Recordatorios

- Túnel nuevo = URL nueva = re-editar callback en Meta. Siempre.
- Si la prueba completa pasa: Fase 1 CERRADA. Siguientes: DT-001 (rastro tool calls), DT-005 (fecha en prompt), y arranque de Fase 2 (recordatorios 24h/2h, hosting real, token system user como default).
