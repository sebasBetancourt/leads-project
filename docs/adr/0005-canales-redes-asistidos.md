# ADR 0005: WhatsApp, Instagram, Facebook y LinkedIn en modo asistido

**Estado:** aceptada (2026-10-30). Reemplaza la versión anterior (envío automático con Playwright).

## Contexto
No hay acceso a las APIs oficiales de estas redes. Automatizar el envío desde la cuenta del dueño
(Playwright con su sesión) va contra los términos de las cuatro plataformas y suele terminar en
bloqueo de la cuenta. El dueño ya aprueba cada mensaje a mano antes de que salga.

## Decisión
- **El sistema no envía por redes.** Arma el mensaje y el enlace que abre el chat
  (`outreach/channels/assisted.py`) y los pone en la pestaña Aprobación de Sheets:
  - WhatsApp: `https://wa.me/<número>?text=<mensaje>`, con el texto ya escrito. Solo celulares (`enrichment/phone.is_mobile`).
  - Instagram: `https://ig.me/m/<usuario>`; el mensaje se pega.
  - Facebook: `https://m.me/<página>` (o la página si no tiene usuario); el mensaje se pega.
  - LinkedIn: el perfil o la página de la empresa; el mensaje se pega.
- El dueño da clic, envía y marca **ENVIADO** en la columna Decisión. `sheets.pull_decisions` lo pasa a `SENT`.
- `config/limits.yaml` sigue aplicando: limita cuántos mensajes por canal aparecen por día en Sheets.
- **El email es el único canal con envío automático** (Gmail SMTP, ver F3), también después de aprobar.

## Consecuencias
- Riesgo de bloqueo de cuentas muy bajo: el envío lo hace una persona, a ritmo humano.
- Exige unos minutos al día del dueño (unos 20-40 clics). Es el precio de no arriesgar sus cuentas.
- No se necesita Playwright ni guardar sesiones de redes.
- Si en el futuro se obtiene WhatsApp Business API, se agrega un adaptador automático sin cambiar el resto.
