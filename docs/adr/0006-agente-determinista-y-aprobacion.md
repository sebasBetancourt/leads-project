# ADR 0006: Agente con investigación determinista y contrato de aprobación en Sheets

**Estado:** aceptada (2026-10-30)

## Contexto
En F2 el agente califica los leads `ENRICHED` y redacta los mensajes, y el dueño los aprueba en Sheets.
Hacía falta decidir:
- cuánto margen tiene el LLM para elegir herramientas;
- cómo evitar que invente datos o canales;
- qué significa cada decisión en Sheets, que es por mensaje, cuando el estado es por lead;
- cómo sincronizar una pestaña editable sin perder ediciones.

## Decisión

### 1. La investigación es determinista
`review_website` y `search_web_presence` llaman las tools con reglas fijas:
- la web propia y una página de tienda o catálogo;
- dos búsquedas con plantilla.

El LLM solo entra en `qualify` y en `draft_messages`, y no elige herramientas (no hay bucle ReAct).
Así el costo por lead es predecible y las pruebas no dependen del modelo. `Tool.as_llm_schema()` sigue
disponible por si más adelante se quiere un agente que las elija.

### 2. Lo verificable lo dice el código, no el modelo
- `enrichment/signals.py` detecta en el HTML, sin LLM:
  - plataformas (Shopify, WooCommerce, Wix...);
  - pasarelas (Wompi, PayU, ePayco, PSE...);
  - carrito, precios, catálogos en PDF y enlaces a WhatsApp o a marketplaces;
  - el año del copyright.

  El modelo recibe estas señales como hechos.
- El contenido de webs y buscadores llega al prompt entre `<<<` y `>>>`, marcado como datos de terceros (prompt injection).
- `_enforce_rules` en `agents/nodes.py` aplica después del modelo:
  - los canales salen solo de los que el lead tiene, sin los de la lista de no contactar;
  - máximo `max_channels_per_lead`;
  - el caso de éxito tiene que existir en `knowledge/`;
  - `qualified = score >= agent.min_score` y que haya algún canal.
- `agents/checks.py` revisa cada mensaje:
  - agrega siempre la línea de baja (Ley 1581);
  - marca falta de asunto, largo por canal, emojis, marcadores sin reemplazar y cifras que no aparecen en la investigación.

  Si hay alertas, se pide una corrección. Si persisten, el mensaje llega a Sheets con la columna `alertas` llena.

### 3. Decisiones en la pestaña Aprobación

| Decisión | Mensaje | Requisito |
|---|---|---|
| `APROBADO` | `APPROVED` (con tus ediciones) | — |
| `REHACER` | `REJECTED`; el agente lo vuelve a redactar | notas obligatorias |
| `DESCARTAR` | `REJECTED` | — |

Cuando a un lead no le quedan mensajes por decidir:

| Resultado de la ronda | Estado del lead |
|---|---|
| Algún `APROBADO` | `APPROVED` (los `REHACER` de ese lead no se vuelven a redactar) |
| Si no, algún `REHACER` | `DRAFTED`: el grafo corto `draft_messages → save` reutiliza la investigación y agrega tus notas al prompt |
| Todos `DESCARTAR` | `UNQUALIFIED` |

Una decisión inválida (texto desconocido o `REHACER` sin notas) no se aplica y se avisa en `alertas`.

### 4. Sincronización: primero se lee y después se escribe
`SheetsSync.sync()`:
1. lee las decisiones y ediciones (`pull_decisions`);
2. reescribe la pestaña Aprobación conservando lo editable de cada fila que sigue pendiente, quita
   las filas ya decididas y agrega al final las nuevas;
3. actualiza la pestaña Leads.

Las columnas fijas van primero (A–I) y las editables al final (J–M).

### 5. Cada lead es un trabajo de la cola
Los trabajos `qualify` y `redraft` van en `jobs` con `dedup_key` = id del lead y un índice único parcial
mientras el trabajo está vivo. El guardado (`LeadRepository.save_agent_result`):
- se hace en una transacción, con el lead bloqueado (`FOR UPDATE`);
- verifica que el lead siga en el estado esperado.

Así el worker y `leadbot qualify` pueden correr a la vez.

### 6. Proveedor de IA: Groq
Se usa Groq por LiteLLM: `LLM_MODEL=groq/...`, con un modelo opcional por tarea (`LLM_MODEL_QUALIFY`, `LLM_MODEL_DRAFT`).

`LLM_JSON_MODE` elige cómo se pide el JSON:
- `schema`: salida estructurada estricta;
- `json_object`: para modelos que solo tienen modo JSON.

Los esquemas de `agents/schemas.py` no tienen valores por defecto porque el modo estricto exige campos obligatorios.

## Consecuencias
- El agente no descubre fuentes nuevas por su cuenta. Si una fuente resulta útil, se agrega como paso fijo.
- Las webs que solo cargan con JavaScript se leen a medias. Las huellas de la plataforma sí se detectan y la señal
  `js_rendered` avisa al modelo. Si no alcanza, se evaluará Playwright más adelante.
- Si alguien edita una fila justo en el segundo entre la lectura y la escritura, esa edición se puede perder.
  Con un solo revisor y sincronizaciones cada 5 minutos el riesgo es bajo. El panel web lo eliminará.
- `messages.original_body` y los eventos `message_edited` (con el diff) permiten medir cuánto se corrige al agente
  y ajustar los prompts.
