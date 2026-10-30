# ADR 0005: WhatsApp, Instagram, Facebook y LinkedIn con automatización de navegador

**Estado:** aceptada (2026-10-30), con riesgo asumido por el dueño del proyecto

## Contexto
No hay acceso a las APIs oficiales de estas redes. El dueño eligió envío automático con límites bajos.

## Decisión
- Playwright con un perfil persistente por red (`sessions/<canal>`). El inicio de sesión es manual, una vez
  (`leadbot login <canal>`).
- Corre en la máquina del usuario, con navegador visible; no en Docker.
- Solo mensajes aprobados, topes diarios bajos, horario hábil, pausas aleatorias y mensajes distintos
  (`config/limits.yaml`, `outreach/guardrails.py`).
- `health_check` por canal: si la sesión cae o aparece un bloqueo, el canal se pausa solo.
- **No** se implementa evasión de detección (huellas falsas, rotación de cuentas o proxies para ocultar la automatización).
- WhatsApp solo a números celulares (`enrichment/phone.is_mobile`).

## Consecuencias
- Automatizar mensajes va contra los términos de servicio de estas plataformas: existe riesgo de
  restricción o bloqueo de las cuentas usadas. Se recomienda usar cuentas de la empresa, no personales.
- El email (F3) es el canal principal y el único que escala sin ese riesgo.
- Si en el futuro se obtiene acceso a WhatsApp Business API, se agrega un adaptador nuevo sin cambiar el resto.
