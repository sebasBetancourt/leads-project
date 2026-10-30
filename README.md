# BrainSol Leads (`leadbot`)

Sistema para conseguir clientes para [Brain Solutions](https://brainsol.vercel.app/) en toda Colombia:

1. **Busca** negocios en Google Maps por sector y ciudad.
2. **Investiga** su web y presencia digital, y extrae sus canales de contacto.
3. **Califica** con IA si necesitan una tienda en línea o un software a medida.
4. **Redacta** un mensaje personalizado con el caso de éxito más parecido.
5. **Tú apruebas** en Google Sheets.
6. **Envía**: el email sale solo; en WhatsApp, Instagram, Facebook y LinkedIn el sistema te abre el chat
   con el mensaje listo y tú das el clic (modo asistido, sin riesgo de bloqueo).

Arranque: **Santander** (área metropolitana de Bucaramanga) y los sectores de los 3 casos de éxito.

Arquitectura completa: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Decisiones: [`docs/adr/`](docs/adr/)

## Estado

| Fase | Qué entrega | Estado |
|---|---|---|
| F0 | Estructura, arquitectura y contratos | ✅ |
| F1 | Leads reales en Google Sheets | ✅ |
| F2 | Agente IA califica y redacta; aprobación | 🚧 código listo, falta corrida real |
| F3 | Envío por email y seguimientos | |
| F4 | WhatsApp, Instagram, Facebook, LinkedIn (asistido) | |
| F5 | Propuesta en PDF y métricas | |

## Estructura

```
config/            sectores, ciudades y límites de envío (YAML, sin tocar código)
knowledge/         quién es BrainSol y los casos de éxito (contexto para la IA)
docs/              arquitectura, modelo de datos y ADRs
src/leadbot/
  core/            configuración, base de datos, puerta única al LLM
  leads/           modelo de datos, estados, repositorio
  discovery/       búsquedas en Google Maps (gosom)
  enrichment/      web del negocio → correos, redes, WhatsApp
  tools/           herramientas que usa el agente (web, búsqueda, casos de éxito)
  agents/          grafo LangGraph, prompts, salidas estructuradas
  proposals/       propuesta en PDF
  outreach/        canales, guardrails, cola y seguimientos
  sheets/          sincronización con Google Sheets
  jobs/            scheduler del worker
  cli.py           un comando por etapa
tests/
```

## Arranque

```bash
cp .env.example .env              # completa los valores
docker compose up -d postgres gmaps
uv sync --extra dev
uv run leadbot migrate                        # crea las tablas
uv run leadbot queries                        # ver las búsquedas que se harán (Santander)
uv run leadbot discover --limit 3             # Google Maps -> leads NEW
uv run leadbot enrich                         # su web -> email, WhatsApp, Instagram, Facebook
uv run leadbot stats                          # cuántos leads hay en cada estado
uv run leadbot import-xlsx Leads.xlsx         # alternativa a discover + enrich: carga una exportación de la pestaña Leads
uv run leadbot research 12                    # F2: investigación de un lead (sin IA, no guarda nada)
uv run leadbot qualify --limit 3 --dry-run    # F2: calificación y mensajes de 3 leads, sin guardar
uv run leadbot qualify --limit 20             # F2: el agente califica y deja mensajes en PENDING_APPROVAL
uv run leadbot sync                           # lee tus decisiones de Sheets y actualiza Aprobación y Leads
docker compose up -d worker                   # lo mismo, automático (discover diario; enrich y qualify cada 10 min; sync cada 5)
```

### Google Sheets

1. En Google Cloud, crea una cuenta de servicio, activa la API de Google Sheets y descarga su JSON
   a `credentials/google-service-account.json`.
2. Crea una hoja de cálculo y compártela (como Editor) con el correo de la cuenta de servicio.
3. Pon el ID de la hoja (lo que va entre `/d/` y `/edit` en la URL) en `GOOGLE_SHEET_ID` del `.env`.

### Aprobar mensajes (pestaña Aprobación)

Cada fila es un mensaje. Puedes reescribir **asunto** y **mensaje**; en **decisión** elige:

| Decisión | Qué pasa |
|---|---|
| `APROBADO` | Queda listo para enviar (email en F3; redes: usa el enlace de **abrir chat**) |
| `REHACER` | El agente lo vuelve a escribir siguiendo tus **notas** (son obligatorias) |
| `DESCARTAR` | No se envía. Si descartas todos los mensajes de un negocio, sale del embudo |

Revisa con cuidado las filas con **alertas** (cifras que no están en la investigación, mensaje muy largo...).
Las filas decididas desaparecen en la siguiente sincronización (cada 5 minutos con el worker).

### IA (Groq)

Crea una key en https://console.groq.com/keys y ponla en `GROQ_API_KEY`. El modelo se elige con
`LLM_MODEL` (por defecto `groq/openai/gpt-oss-120b`); si usas un modelo sin salida estructurada estricta,
pon `LLM_JSON_MODE=json_object`.

## Pruebas

```bash
uv run pytest                     # unitarias (sin red ni base de datos)
uv run ruff check src tests

# Con Postgres (cola, agente, aprobación): base desechable sin contraseña, solo en 127.0.0.1:55432
docker run -d --name leadbot-test-pg -e POSTGRES_USER=leadbot -e POSTGRES_DB=leadbot_test \
  -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+psycopg://leadbot@127.0.0.1:55432/leadbot_test uv run pytest
```

CI (GitHub Actions) corre todo, incluido Postgres y la migración de ida y vuelta.
