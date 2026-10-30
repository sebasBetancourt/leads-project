# Arquitectura: BrainSol Leads (`leadbot`)

Estado: **aprobada** (2026-10-30). Las decisiones de fondo están en [`adr/`](adr/).

## 1. Objetivo

Encontrar negocios en toda Colombia que se beneficiarían de una tienda en línea o un software a
medida, investigarlos con IA, redactar un mensaje personalizado con el caso de éxito más
parecido, y enviarlo por email, WhatsApp, Instagram, Facebook o LinkedIn, **siempre con
aprobación humana antes de enviar**. El email sale solo; las redes son **asistidas** (el sistema
abre el chat con el mensaje listo y el dueño da el clic).

Arranque: Santander (área metropolitana de Bucaramanga primero) y los sectores parecidos a los
3 casos de éxito: repuestos y autopartes, comercio minorista, joyería y relojería.

## 2. Principios

1. **Postgres es la fuente de verdad.** Google Sheets es solo la vista y el lugar de aprobación.
2. **Nada sale sin aprobación.** Todo mensaje pasa por `PENDING_APPROVAL` en Sheets.
3. **Ningún bot escribe desde tus redes.** WhatsApp, Instagram, Facebook y LinkedIn son asistidos (ADR 0005).
4. **Todo envío pasa por guardrails**: horario, tope diario, pausa por canal y lista de no contactar.
5. **Cada etapa corre sola y se puede reintentar** (`leadbot discover`, `enrich`, `qualify`...).
6. **Proveedores intercambiables**: el LLM (LiteLLM), los canales (adaptadores) y la búsqueda web (tools).
7. **No reinventar**: Google Maps lo resuelve gosom; nosotros orquestamos.

## 3. Flujo

```
 config/sectors.yaml × config/cities.yaml
            │  query_builder: "academias en Pereira", "ferreterías en Cali"...
            ▼
 [1] DISCOVERY   discovery/      gosom REST API ──► companies (NEW), sin duplicados por place_id/teléfono
            ▼
 [2] ENRICHMENT  enrichment/     crawler de la web ──► contact_channels: email, IG, FB, LinkedIn, WhatsApp
            ▼                                          (ENRICHED, o UNQUALIFIED si no hay cómo contactar)
 [3] AGENTE      agents/ + tools/
       review_website ─┐
                       ├─► qualify ──► draft_messages ──► save      research + messages (PENDING_APPROVAL)
  search_web_presence ─┘      └─ no califica ─────────► save       (UNQUALIFIED)
            ▼
 [4] APROBACIÓN  sheets/         pestaña "Aprobación": editas, marcas APROBADO / RECHAZADO
            ▼
 [5] OUTREACH    outreach/       guardrails (horario, tope diario, no contactar)
       ├─ email: cola (SKIP LOCKED) ─► email_gmail, envío automático (F3)
       └─ redes: channels/assisted.py arma el enlace del chat en Sheets ─► tú das clic,
                 envías y marcas ENVIADO (F4)
            ▼
 [6] RESPUESTAS  email: IMAP automático · redes: las marcas en Sheets ─► REPLIED / MEETING / WON / LOST / OPT_OUT
            ▼
 [7] SEGUIMIENTO sequences.py    email día 3 y día 7 si no hay respuesta (también con aprobación)
```

## 4. Módulos

| Módulo | Responsabilidad | Depende de | Origen del patrón |
|---|---|---|---|
| `core/` | Settings (.env), YAML, conexión a Postgres, puerta única al LLM, logging | — | AgentSDR (`db.ts` único, `ai/` único) |
| `leads/` | Modelo de datos, máquina de estados, repositorio | `core` | kaymen99 (`LeadLoaderBase`) |
| `discovery/` | Armar búsquedas y hablar con gosom | `core`, `leads`, `enrichment.phone` | gosom (servicio) |
| `enrichment/` | Visitar webs, extraer contactos, normalizar teléfonos +57 | `core`, `leads` | fujiwarachoki/leadgen |
| `tools/` | Herramientas que el agente decide usar: `knowledge_base`, `web_reader`, `web_search` | `core`, `enrichment` | kaymen99 (`src/tools/`) |
| `agents/` | Grafo LangGraph por lead, prompts, salidas estructuradas | `core`, `tools`, `leads` | kaymen99 (`graph.py`, `nodes.py`, `state.py`) |
| `proposals/` | Propuesta en PDF por lead | `agents.schemas` | — |
| `outreach/` | Adaptadores de canal, guardrails, cola, seguimientos | `core`, `leads` | AgentSDR (send guardrails, SKIP LOCKED, `PAUSE_*`) |
| `sheets/` | Sincronización Postgres ⇄ Google Sheets | `leads` | kaymen99 (`leads_loader/google_sheets.py`) |
| `jobs/` | Scheduler del worker | todos | AgentSDR (scheduler único) |
| `cli.py` | Un comando por etapa | todos | — |

**Regla de dependencias:** `core` no importa a nadie; `leads` solo a `core`; los canales de
`outreach/` no conocen al agente; el agente no envía nada (solo deja mensajes en `PENDING_APPROVAL`).

### `tools/` vs `enrichment/`

- **enrichment** es un paso fijo del pipeline: corre siempre, igual para todos los leads, sin LLM.
- **tools** son capacidades que el LLM decide usar durante la investigación ("lee su página de
  productos", "busca si vende en Rappi"). Reutilizan el código de `enrichment/`, no lo duplican.
- Para agregar una tool: subclase de `tools.base.Tool` (`name`, `description`, `args_schema`,
  `run`) y registrarla en `tools/registry.py`. Una tool que falla devuelve `ok=False`, nunca
  tumba al agente.

## 5. Estados

Lead (`leads/status.py`):

```
NEW → ENRICHED → QUALIFIED → DRAFTED → PENDING_APPROVAL → APPROVED → CONTACTED → REPLIED → MEETING → WON
 │        │                                 │    ▲                        │          │          │
 └► UNQUALIFIED ◄───────────────────────────┤    └─ rechazado con notas   └► LOST ◄──┴──────────┘
                                            └─► (vuelve a DRAFTED)
OPT_OUT: desde cualquier estado, final e irreversible.
```

Mensaje: `DRAFT → PENDING_APPROVAL → APPROVED → QUEUED → SENT | FAILED`, o `REJECTED`, o
`CANCELLED` si el lead respondió u optó por salir antes del envío.

## 6. Datos

Ver [`DATA_MODEL.md`](DATA_MODEL.md).

## 7. Despliegue

```
docker-compose
├── postgres   :5432   datos
├── gmaps      :8080   gosom/google-maps-scraper:v1.18.1 (REST API + UI)
├── worker             scheduler: discovery, enrichment, agente, Sheets, email
└── app (cli)          docker compose run --rm app leadbot <comando>

Tu navegador o celular
└── enlaces de la pestaña Aprobación (wa.me, ig.me, m.me): el envío por redes lo haces tú
```

Ver [ADR 0005](adr/0005-canales-redes-asistidos.md).

## 8. Seguridad, cumplimiento y riesgos

| Tema | Medida |
|---|---|
| Ley 1581 (datos personales) | Solo datos públicos de negocios; línea de baja en cada mensaje; `suppressions` revisada antes de cada envío; `OPT_OUT` irreversible |
| Secretos | `.env` y `credentials/` fuera de git; contraseña de aplicación de Gmail, nunca la principal |
| Bloqueo de cuentas en redes | Modo asistido: ningún bot envía desde tus cuentas; tú das el clic. Topes diarios por canal en Sheets y mensajes distintos. |
| Reputación del dominio/Gmail | Calentamiento progresivo (30 → 80/día), `List-Unsubscribe`, seguimientos limitados a 2 |
| Alucinaciones del LLM | Salida estructurada validada; prompts prohíben inventar datos; aprobación humana |
| Costos de IA | Solo se califican leads ENRICHED; tools devuelven texto recortado; modelo cambiable |

## 9. Fases

| Fase | Entrega | Módulos |
|---|---|---|
| **F0** ✅ | Estructura, arquitectura, contratos, config, conocimiento, tests base | todos (esqueleto) |
| **F1** ✅ | Leads reales en Sheets | `discovery`, `enrichment`, `leads.repository`, `sheets.push`, migraciones Alembic, `jobs`. Primera corrida (2026-10-30, Bucaramanga): 3 búsquedas, 48 negocios, 41 con canal de contacto. |
| **F2** | El agente califica y redacta; apruebas en Sheets | `tools`, `agents`, `sheets.pull_decisions` |
| **F3** | Email con seguimientos y lectura de respuestas | `channels/email_gmail`, `outreach.queue`, `sequences` |
| **F4** | WhatsApp, Instagram, Facebook, LinkedIn asistidos; descubrir leads directamente en Instagram y Facebook | `channels/assisted` (listo), columna "abrir chat" y ENVIADO en Sheets |
| **F5** | Propuesta PDF y métricas | `proposals`, pestaña Métricas |

## 10. Referencias open source

- [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) (MIT): se usa como servicio, sin modificar.
- [Kandid-ai/AgentSDR](https://github.com/Kandid-ai/AgentSDR) (MIT): organización por dominios, guardrails, cola con `SKIP LOCKED`, kill switches.
- [kaymen99/sales-outreach-automation-langgraph](https://github.com/kaymen99/sales-outreach-automation-langgraph) (**sin licencia**): solo se tomó el patrón de diseño; no se copió código.
- [fujiwarachoki/leadgen](https://github.com/fujiwarachoki/leadgen) (MIT): idea del paso de extracción de correos desde la web.
