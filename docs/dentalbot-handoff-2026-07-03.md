# DentalBot — Handoff para 2026-07-03 (prueba real Meta)

## Estado al cierre del 02/07

- Código de Fase 1 completo: 5 tools reales, guard de double-booking validado contra Postgres real (carrera con 2 conexiones, 5 corridas sin flakiness), validaciones de agendar_cita (pasado/grid/horizonte), 104 tests + 1 skip.
- Migración `b13cc9253686` aplicada a Postgres (indexdef verificado con WHERE cancelled/no_show).
- Infra dev lista: imagen api reconstruida (fix ModuleNotFoundError anthropic), logging root configurado (fix logs INFO invisibles), seed de Clínica Dental Demo (whatsapp_phone_id=1162389980294305, America/Lima, horarios L-V 09-13/15-19 + sáb 09-13, 3 tratamientos).
- Meta: app "Dentaleads" (modo Desarrollo), número de prueba activo, hello_world recibido en el móvil. Webhook AÚN NO configurado.
- .env: WHATSAPP_ACCESS_TOKEN (temporal 24h — VENCIDO mañana, regenerar), WHATSAPP_APP_SECRET real, ANTHROPIC_API_KEY cargada (verificar saldo > $0 en platform.claude.com).

## Mañana, en orden

1. **Regenerar token de Meta** (el de 24h habrá vencido): app Dentaleads → WhatsApp → API Setup → Generar nuevo token → actualizar .env.
2. **Rotar la ANTHROPIC_API_KEY** (quedó expuesta en una captura): console → API Keys → eliminar y crear nueva → .env. Confirmar saldo cargado.
3. Levantar todo:
   - `docker compose up -d --build api`
   - `cloudflared tunnel --url http://localhost:8000` (anotar URL)
   - `docker compose logs -f api` (en otra terminal)
4. **Configurar webhook en Meta**: WhatsApp → Configuración → Webhook → Callback URL = `https://<subdominio>.trycloudflare.com/webhook/whatsapp`, Verify token = `dev-verify-token` → verificar (se ve el GET en logs) → suscribirse al campo **messages**.
5. **La prueba**: enviar "hola" desde el móvil al número de prueba. Ver en logs: POST entrante → llamada a Claude → respuesta en WhatsApp. Probar flujo completo: saludo → consultar tratamiento → verificar disponibilidad → agendar cita → verificar fila en appointments.
6. Si falla: revisar en este orden — firma (403 = APP_SECRET), tenant (warning "unknown tenant" = phone_number_id no coincide), 500 tras el log de entrada (= ANTHROPIC_API_KEY/saldo).

## Pendientes tras la prueba

- DT nuevo: try/except alrededor de la llamada al SDK en handle() (hoy un error de Anthropic → 500 al webhook → Meta reintenta y puede marcar el webhook como no saludable).
- DT-001: rastro de tool calls no persistido (decidir: Fase 1 o Fase 2).
- Nota: cloudflared cambia de URL en cada arranque — reconfigurar el callback en Meta cada vez, o montar túnel con nombre si se vuelve rutina.
