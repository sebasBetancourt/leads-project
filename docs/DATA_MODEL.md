# Modelo de datos

Definido en `src/leadbot/leads/models.py`. Migraciones con Alembic en `migrations/versions/`
(0001 esquema inicial, 0002 agente y aprobación).

```
companies 1───* contact_channels
    │ 1             │ 1
    │               │
    ├───* research  │
    │               │
    └───* messages *┘ (cada mensaje va a un canal concreto)
            │
events  *───┘ (bitácora por lead y por mensaje)

suppressions   lista de no contactar (independiente: bloquea por valor)
jobs           cola de trabajos (independiente)
```

| Tabla | Qué guarda | Claves importantes |
|---|---|---|
| `companies` | Un negocio de Google Maps | `place_id` único; `phone_e164` indexado para deduplicar; `status`; `score`; `raw` con la fila original de gosom |
| `contact_channels` | Cada forma de contactarlo | único por (`company_id`, `channel`, `value`); `source` = gmaps / website / manual |
| `research` | Lo que concluyó el agente | `score`, `qualified`, `needs`, `case_study`, `model` (qué LLM), `tokens` y `cost_usd`; `data` guarda la calificación completa, las señales de su web y la investigación (se reutiliza al rehacer) |
| `messages` | Un mensaje por canal y paso | `step` 0 = primer contacto, 1+ = seguimientos; `status`; `original_body` (lo que redactó el agente); `review_flags` (alertas de `agents/checks.py`); `decision` y `reviewer_notes` desde Sheets |
| `events` | Todo lo que pasa | `kind`: status_changed, sent, failed, replied, opted_out... |
| `suppressions` | No contactar | `value` único (email, teléfono E.164 o URL) |
| `jobs` | Cola de trabajos | `kind` (qualify, redraft...), `status` (pending, running, done, failed), `run_after`, `attempts`; se toma con `FOR UPDATE SKIP LOCKED`; `dedup_key` único mientras el trabajo está vivo (`uq_jobs_alive`) |

## Deduplicación

1. Mismo `place_id` de Google Maps → mismo negocio (se actualiza).
2. Sin `place_id`, mismo `phone_e164` → mismo negocio.
3. Un negocio que aparece en dos búsquedas conserva la primera `source_query`.

## Qué es editable desde Sheets

Solo `messages.subject`, `messages.body`, la decisión de aprobación (`APROBADO` / `REHACER` /
`DESCARTAR`), `reviewer_notes` y el estado de respuesta en redes (F4). Todo lo demás se sobrescribe
desde Postgres en cada sincronización. Cada edición queda en `events` (`message_edited`, con el diff)
y cada decisión en `message_reviewed`. Reglas completas en [ADR 0006](adr/0006-agente-determinista-y-aprobacion.md).
