# Modelo de datos

Definido en `src/leadbot/leads/models.py`. Migraciones con Alembic en `migrations/versions/`
(0001 esquema inicial, 0002 agente y aprobación, 0003 envío de email).

```
companies 1───* contact_channels
    │ 1             │ 1
    │               │
    ├───* research  │
    │               │
    └───* messages *┘ (cada mensaje va a un canal concreto)
            │
events  *───┘ (bitácora por lead y por mensaje)

suppressions        lista de no contactar (independiente: bloquea por valor)
jobs                cola de trabajos (independiente)
runtime_flags       pausas que se cambian sin reiniciar el worker (independiente)
mail_cursor         hasta qué correo de la bandeja ya se leyó (independiente)
worker_heartbeats   última corrida de cada tarea del worker (independiente)
```

| Tabla | Qué guarda | Claves importantes |
|---|---|---|
| `companies` | Un negocio de Google Maps | `place_id` único; `phone_e164` indexado para deduplicar; `status`; `score`; `raw` con la fila original de gosom |
| `contact_channels` | Cada forma de contactarlo | único por (`company_id`, `channel`, `value`); `source` = gmaps / website / manual; `invalid` = la dirección rebotó y no se vuelve a usar |
| `research` | Lo que concluyó el agente | `score`, `qualified`, `needs`, `case_study`, `model` (qué LLM), `tokens` y `cost_usd`; `data` guarda la calificación completa, las señales de su web y la investigación (se reutiliza al rehacer) |
| `messages` | Un mensaje por canal y paso | `step` 0 = primer contacto, 1+ = seguimientos; `status`; `original_body` (lo que redactó el agente); `review_flags` (alertas de `agents/checks.py`); `decision` y `reviewer_notes` desde Sheets; `external_id` = `Message-ID` del email, guardado **antes** de enviar (único); `reply_at` = cuándo respondió; `sent_at` en UTC. Un rebote deja `status = SENT` con `error` lleno |
| `events` | Todo lo que pasa | `kind`: status_changed, message_drafted, queued, sent, send_failed, send_blocked, send_requeued, replied, opted_out, bounced, auto_reply, reply_unmatched, message_cancelled, alert... (los `alert` no tienen lead: se ven en la pestaña Control) |
| `suppressions` | No contactar | `value` único (email, teléfono E.164 o URL) |
| `jobs` | Cola de trabajos | `kind` (qualify, redraft, followup, send), `status` (pending, running, done, failed, needs_review), `run_after`, `attempts`; se toma con `FOR UPDATE SKIP LOCKED`; `dedup_key` único mientras el trabajo está vivo (`uq_jobs_alive`). Los `send` nunca se reintentan solos si el worker muere a mitad: pasan a `needs_review` (ADR 0007) |
| `runtime_flags` | Pausas (`pause_all`, `pause_email`...) | `key` primaria; el `.env` manda sobre ellas |
| `mail_cursor` | Dónde va la lectura de la bandeja | `folder` primaria; `last_uid` y `uidvalidity` de IMAP |
| `worker_heartbeats` | Salud del worker | `task` primaria; `last_run`, `ok`, `detail`; la tarea `tick` alimenta `leadbot worker-health` |

## Deduplicación

1. Mismo `place_id` de Google Maps → mismo negocio (se actualiza).
2. Sin `place_id`, mismo `phone_e164` → mismo negocio.
3. Un negocio que aparece en dos búsquedas conserva la primera `source_query`.

## Qué es editable desde Sheets

Solo `messages.subject`, `messages.body`, la decisión de aprobación (`APROBADO` / `REHACER` /
`DESCARTAR`), `reviewer_notes`, las pausas de la pestaña Control (`runtime_flags`) y el estado de respuesta en redes (F4). Todo lo demás se sobrescribe
desde Postgres en cada sincronización. Cada edición queda en `events` (`message_edited`, con el diff)
y cada decisión en `message_reviewed`. Reglas completas en [ADR 0006](adr/0006-agente-determinista-y-aprobacion.md).
