# Modelo de datos

Definido en `src/leadbot/leads/models.py`. Las migraciones se generan con Alembic en F1.

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
| `research` | Lo que concluyó el agente | `score`, `qualified`, `needs`, `case_study`, `model` (qué LLM) |
| `messages` | Un mensaje por canal y paso | `step` 0 = primer contacto, 1+ = seguimientos; `status`; `reviewer_notes` desde Sheets |
| `events` | Todo lo que pasa | `kind`: status_changed, sent, failed, replied, opted_out... |
| `suppressions` | No contactar | `value` único (email, teléfono E.164 o URL) |
| `jobs` | Cola de trabajos | `kind`, `status`, `run_after`, `attempts`; se toma con `FOR UPDATE SKIP LOCKED` |

## Deduplicación

1. Mismo `place_id` de Google Maps → mismo negocio (se actualiza).
2. Sin `place_id`, mismo `phone_e164` → mismo negocio.
3. Un negocio que aparece en dos búsquedas conserva la primera `source_query`.

## Qué es editable desde Sheets

Solo `messages.subject`, `messages.body`, la decisión de aprobación, `reviewer_notes` y el estado
de respuesta en redes. Todo lo demás se sobrescribe desde Postgres en cada sincronización.
