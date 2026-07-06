# DentalBot — Handoff 2026-07-02 (cierre de sesión)

## Estado del repo (main, limpio)

- `963b45d` Fase 1: agendar_cita real con guard de double-booking + tests
- `4b2dd80` Fase 1: double-booking guard — índice único parcial + constante SLOT_FREEING_STATUSES + tests
- Suite: **100/100 tests verdes** (SQLite in-memory).

## Decisiones de diseño tomadas hoy

- Guard anti-doble-reserva: índice único parcial `uq_appointments_tenant_slot (tenant_id, scheduled_at) WHERE status NOT IN ('cancelled', 'no_show')`. Fuente de verdad única: `SLOT_FREEING_STATUSES` en `app/models/appointment.py`, usada por el índice y por availability.py.
- `agendar_cita`: INSERT con SAVEPOINT (`begin_nested()`), captura `IntegrityError` fuera del `async with`, devuelve `slot_no_disponible` + alternativas del mismo día local. La sesión (compartida en ToolContext) sigue usable tras el conflicto.
- Convención tz: `scheduled_at` queda aware en tz del tenant (ZoneInfo, no pytz). Helper compartido `resolve_clinic_timezone(clinic)` en availability.py.
- Tratamiento no encontrado: agenda con `treatment_id=None` (columna nullable, SET NULL) + aviso sugiriendo `consultar_tratamiento`. No bloquea.
- `tenant_id` y `lead` NUNCA vienen del modelo: se inyectan vía ToolContext (lead ya upserted por whatsapp_number en handle() paso 2).

## EN CURSO — primera tarea de mañana

Claude Code confirmó que `_handle_agendar_cita` NO valida: fecha pasada, alineación al grid, ni horizonte máximo. Se le dijo "sí, agrega las tres validaciones" pero la sesión cerró antes de implementar. Prompt listo para pegar en Claude Code:

> Agrega a _handle_agendar_cita, en orden parse → pasada → grid → horizonte → tratamiento → insert:
> 1. Fecha pasada: comparar el datetime COMPLETO contra now() en la tz del tenant (caso frontera: hoy a las 09:00 cuando son las 14:00). Reutilizar la semántica de availability.py:171, no duplicarla. Retorno: {"status": "fecha_pasada"}.
> 2. Grid: derivar la duración de slot de donde ya la usa AvailabilityService (nada de hardcodear 30 en el handler). Validar minuto % slot_duration == 0. Retorno: {"status": "hora_fuera_de_grid"} (o consistente con los status existentes).
> 3. Horizonte: nueva constante MAX_BOOKING_HORIZON_DAYS = 90 junto a las constantes de dominio, comentario de que puede migrar a clinic.config. Retorno: {"status": "fecha_fuera_de_horizonte"}.
> Las tres devuelven JSON al modelo, nunca excepción. Tests: uno por validación + frontera "hoy pero hora ya pasada". Suite completa y muéstrame el diff antes de commitear.

Commit sugerido: `Fase 1: validaciones de agendar_cita (pasado, grid, horizonte) + tests`.

## Pendientes para cerrar Fase 1 (tras las validaciones)

1. **Docker arriba** → `alembic upgrade head` (migración `b13cc9253686` existe pero NO está aplicada a Postgres real) → test de carrera real: dos INSERT concurrentes contra Postgres, marcado `skipif` sin DATABASE_URL.
2. **Prueba real contra Meta** con el número de prueba (pendiente desde la pérdida del móvil).
3. **CLAUDE.md / deuda técnica:** DT-001 (rastro de tool calls no persistido) y alinear dependencia `anthropic` con uv/pyproject.toml.

## Lecciones de proceso de hoy (mantener)

- Grep antes de asumir literales (así se cayó el bug de `no_show`).
- Verificación empírica antes de aceptar correcciones de review (el "bug" del día UTC resultó falsa alarma; el cambio quedó como simplificación defensiva documentada en el test).
- Constantes de dominio con fuente única de verdad; las migraciones quedan como snapshot congelado.
