# ADR 0007: Envío de email sin dobles envíos, lectura de respuestas y seguimientos

**Estado:** aceptada (2026-10-30). Implementada en la F3; falta validarla con una cuenta de Gmail real (ver Consecuencias).

## Contexto
La F3 envía solo los emails aprobados, lee lo que contestan y escribe los seguimientos. Es la primera vez que el
sistema hace algo que no se puede deshacer: un email enviado dos veces, o uno a quien pidió la baja, daña la
reputación de la cuenta y puede incumplir la Ley 1581. Hacía falta decidir:
- cómo garantizar que un mensaje no salga dos veces aunque el worker se caiga a mitad de un envío;
- cuándo se bloquea un envío y qué pasa con el mensaje mientras tanto;
- cómo se interpreta una respuesta sin depender del LLM;
- cómo se programan los seguimientos sin romper la regla de dependencias de [ARCHITECTURE.md](../ARCHITECTURE.md);
- cómo parar el envío sin reiniciar nada.

## Decisión

### 1. Un trabajo `send` por mensaje, encolado desde `outreach`
`outreach.sender.enqueue_approved` pasa a `QUEUED` los emails `APPROVED` y encola un trabajo `send` con
`dedup_key = id del mensaje`. Corre al inicio de cada ciclo de envío, no dentro de `leads.approval`: `leads` solo
depende de `core` y no puede importar `outreach`. El costo es hasta un minuto de espera entre aprobar y quedar en cola.

### 2. Las puertas globales se revisan antes de tomar el trabajo
Pausa, horario, tope diario (con calentamiento) y ritmo se evalúan antes de `claim_next`. Si alguna bloquea, el
trabajo sigue `pending` y no gasta intentos; solo los errores de SMTP los gastan. Lo que depende del mensaje
(destinatario en `suppressions`, dirección inválida, lead que ya respondió o salió) se revisa después de tomarlo, y
cancela el mensaje (`CANCELLED`).

El ritmo entre envíos sale de `guardrails.pacing_delay`: una pausa aleatoria entre mensajes pero **estable** entre
ciclos, derivada del id del último mensaje enviado. Si se sorteara en cada ciclo (cada minuto), casi siempre
saldría una pausa corta. Se calcula desde la base de datos, así que sobrevive a reinicios.

### 3. Garantía contra envíos dobles
1. El `Message-ID` se genera y se guarda en `messages.external_id` **antes** de abrir la conexión SMTP.
2. El envío ocurre sin ninguna transacción abierta; el resultado se anota en una segunda transacción.
3. Los trabajos de `queue.NO_AUTO_RETRY` (`send`) no los devuelve `release_stale`. Si el worker muere a mitad de un
   envío, `quarantine_stale` los deja en `needs_review` y se crea una alerta.
4. `leadbot send --reconcile` busca ese `Message-ID` en la carpeta Enviados (la marcada `\Sent`, que en cuentas en
   español es `[Gmail]/Enviados`). Si está, el mensaje salió y se marca `SENT`.
5. Si **no** está, el sistema **no** lo reenvía solo. Una persona confirma con `--requeue --live`. Es una desviación
   del borrador del sprint, que reencolaba automáticamente: Gmail podría reescribir el `Message-ID`, y entonces
   reencolar sería justo el envío doble que se quiere evitar. Es preferible que un email no salga a que salga dos veces.

Errores de cuenta (login rechazado, cuota diaria de Gmail) **no** son del mensaje: pausan el canal, dejan el trabajo
`pending` sin gastar un intento (`queue.defer`) y el mensaje espera en `QUEUED`.

### 4. Respuestas: reglas primero, nunca el LLM para una baja
`outreach.replies` lee `INBOX` por IMAP en solo lectura desde el último UID guardado (`mail_cursor`), y clasifica cada
correo con reglas sobre el texto **nuevo** (sin lo citado, que contiene nuestra propia línea de baja):

| Clase | Cómo se reconoce | Efecto |
|---|---|---|
| `BOUNCE` | informe de entrega o remitente `mailer-daemon`, estado 5.x.x | contacto `invalid`, a `suppressions`, se cancelan sus pendientes; el lead pasa a `LOST` solo si no le queda otro canal en juego |
| `SOFT_BOUNCE` / `AUTO_REPLY` | estado 4.x.x, `Auto-Submitted`, `Precedence`, asunto "respuesta automática"... | evento `auto_reply`: el seguimiento se aplaza 2 días; el estado no cambia |
| `OPT_OUT` | "NO", "no gracias", "no me interesa", "darme de baja", `unsubscribe`, asunto `baja`... | `OPT_OUT` irreversible, **todos** los contactos del lead a `suppressions`, pendientes cancelados |
| `REPLY` | todo lo demás | `CONTACTED → REPLIED`, `reply_at`, pendientes cancelados |

Emparejamiento: `In-Reply-To` / `References` contra `messages.external_id`; si no, la dirección del remitente contra
`contact_channels`; si no, otra dirección **del mismo dominio de empresa** (nunca dominios gratuitos como gmail.com ni
si hay más de un negocio con ese dominio). Lo que no empareja pero tiene el asunto de algo que enviamos queda como
evento `reply_unmatched`. Todo es idempotente: reprocesar un correo no repite el efecto, y el cursor no avanza más
allá de un correo que no se pudo procesar.

Desviaciones del borrador: un rebote deja el mensaje en `SENT` con `error` lleno (el envío sí ocurrió, y el tope
diario cuenta envíos reales) en vez de pasarlo a `FAILED`; y solo se cancelan los pendientes **del contacto que
rebotó**, no los de todo el lead.

### 5. Seguimientos: `outreach` decide cuándo, el agente redacta
`sequences.due_follow_ups` calcula qué toca (sin LLM); `schedule_follow_ups` encola trabajos `followup`;
`agents.followup` los convierte en borradores `PENDING_APPROVAL` con un grafo corto
(`draft_follow_up → save_follow_up`) que reutiliza `research.data`. Así `outreach` sigue sin conocer al agente.

- Los días cuentan desde el **primer email** (`follow_up_days: [3, 7]` = día 3 y día 7), como dice `limits.yaml`, y
  son días calendario; los envíos ya solo salen en la ventana de lunes a viernes. El borrador hablaba de días
  hábiles desde el paso anterior.
- Siempre pasan al menos 24 horas desde el paso anterior. Una respuesta automática aplaza 2 días.
- El asunto (`Re: ...`) lo arma el código y el mensaje sale con `In-Reply-To` / `References`: queda en el mismo hilo.
- El lead sigue `CONTACTED`. `REHACER` en un seguimiento vuelve a encolarlo con tus notas; `DESCARTAR` termina la secuencia.
- Cuando se acaban los pasos (o se descarta uno) y pasan `close_after_days` sin respuesta, el lead pasa a `LOST`
  con motivo `sin_respuesta`, salvo que le quede algo en camino u otro canal enviado.
- Corre cada 30 minutos (no una vez al día) porque agendar es idempotente y así un `REHACER` se atiende pronto.

### 6. Pausa en caliente y protecciones de reputación
`runtime_flags` (editable desde la pestaña Control de Sheets o con `leadbot pause|resume`) se suma al `.env`; **el
`.env` manda**: si dice `PAUSE_*=true`, no se quita desde Sheets. El email se pausa solo (con una alerta) si:
- el login de Gmail falla, o Gmail informa que se agotó la cuota diaria;
- hay más de `reputation.max_bounces_per_day` (3) rebotes definitivos en 24 horas;
- la revisión horaria de la cuenta (`gmail_account`) no puede entrar a SMTP o IMAP.
Una pausa automática nunca se quita sola: la quita una persona.

### 7. Operación
- **Latido:** cada tarea del worker anota `worker_heartbeats`; `leadbot worker-health` es el healthcheck de compose.
- **Alertas:** eventos `alert` (sin repetirse 6 horas) en la pestaña Control: envíos fallidos o a medias, cola
  estancada más de 4 días sin pausa, tarea que falló. Un worker muerto no puede avisar de sí mismo: lo cubre el
  healthcheck y la hora de "última sincronización" de esa pestaña.
- **Logs:** `LOG_FORMAT=json` da un objeto por línea; los envíos llevan `company_id`, `message_id` y `job_id`.
- **Despliegue:** puertos de Postgres y gosom solo en `127.0.0.1`, contenedor sin root, `uv` fijado en la versión
  que generó `uv.lock` y `--locked`, y un servicio `backup` con `pg_dump` diario (7 copias).

## Consecuencias
- **No validado con Gmail real.** Todo está probado con SMTP e IMAP falsos. Antes de producción hay que comprobar
  en una cuenta de prueba que Gmail conserva el `Message-ID` que se le envía (de eso depende emparejar respuestas por
  encabezado y reconciliar), que las respuestas llegan a `INBOX`, y cómo se ven los rebotes reales.
- Una baja detectada por reglas puede ser un falso positivo ("no me interesa ahora, escríbeme en marzo" cuenta como
  baja). Se acepta: `OPT_OUT` es irreversible y es el error barato frente a insistirle a quien dijo que no.
- El healthcheck solo marca el contenedor como `unhealthy`; Docker Compose no lo reinicia por sí solo.
- Los datos de rebote dependen de que Gmail entregue el informe de entrega a `INBOX`.
- Sigue siendo un solo worker (ADR 0003).
- El cálculo de envíos de hoy y de días activos se hace en Python sobre los envíos del canal. Con cientos de
  mensajes es trivial; si algún día son decenas de miles se pasa a SQL.
