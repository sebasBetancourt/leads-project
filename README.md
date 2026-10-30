# BrainSol Leads (`leadbot`)

Sistema para conseguir clientes para [Brain Solutions](https://brainsol.vercel.app/) en toda Colombia:

1. **Busca** negocios en Google Maps por sector y ciudad.
2. **Investiga** su web y presencia digital, y extrae sus canales de contacto.
3. **Califica** con IA si necesitan una tienda en línea o un software a medida.
4. **Redacta** un mensaje personalizado con el caso de éxito más parecido.
5. **Tú apruebas** en Google Sheets.
6. **Envía** por email, WhatsApp, Instagram, Facebook o LinkedIn, con límites seguros y seguimientos.

Arquitectura completa: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Decisiones: [`docs/adr/`](docs/adr/)

## Estado

| Fase | Qué entrega | Estado |
|---|---|---|
| F0 | Estructura, arquitectura y contratos | ✅ |
| F1 | Leads reales en Google Sheets | ⏳ |
| F2 | Agente IA califica y redacta; aprobación | |
| F3 | Envío por email y seguimientos | |
| F4 | WhatsApp, Instagram, Facebook, LinkedIn | |
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
docker compose run --rm app leadbot queries --max-priority 1   # ver las búsquedas que se harán
```

Desarrollo local (requiere [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --extra dev
uv run pytest
uv run leadbot --help
```
