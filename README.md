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
| F1 | Leads reales en Google Sheets | 🟡 código y pruebas listos; falta conectar tu hoja |
| F2 | Agente IA califica y redacta; aprobación | |
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
uv run leadbot sync                           # Postgres -> Google Sheets
docker compose up -d worker                   # lo mismo, automático (discover diario, enrich cada 10 min)
```

### Google Sheets

1. En Google Cloud, crea una cuenta de servicio, activa la API de Google Sheets y descarga su JSON
   a `credentials/google-service-account.json`.
2. Crea una hoja de cálculo y compártela (como Editor) con el correo de la cuenta de servicio.
3. Pon el ID de la hoja (lo que va entre `/d/` y `/edit` en la URL) en `GOOGLE_SHEET_ID` del `.env`.

Pruebas: `uv run pytest` · Lint: `uv run ruff check src tests`
