"""CLI: leadbot <comando>. Cada comando es una etapa del pipeline y se puede correr sola."""

import sys
from pathlib import Path

import typer

from leadbot.core.logging import setup_logging
from leadbot.discovery.query_builder import build_queries
from leadbot.leads.status import Channel

app = typer.Typer(help="BrainSol Leads: prospección automatizada para Colombia", no_args_is_help=True)


@app.callback()
def main(verbose: bool = False) -> None:
    if hasattr(sys.stdout, "reconfigure"):  # consola de Windows (cp1252): que las tildes no tumben el comando
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging("DEBUG" if verbose else "INFO")


@app.command()
def queries(
    sector: list[str] | None = typer.Option(None, help="Clave de sectors.yaml"),
    city: list[str] | None = typer.Option(None, help="Nombre de cities.yaml"),
    max_priority: int = 1,
) -> None:
    """Muestra las búsquedas que se enviarían a Google Maps (no ejecuta nada)."""
    found = build_queries(sectors=sector, cities=city, max_priority=max_priority)
    for q in found:
        typer.echo(f"[{q.sector}] {q.keyword}")
    typer.echo(f"\n{len(found)} búsquedas")


@app.command()
def migrate() -> None:
    """Crea o actualiza las tablas en Postgres."""
    from leadbot.core.migrate import upgrade

    upgrade()
    typer.echo("Base de datos al día")


@app.command()
def discover(
    sector: list[str] | None = typer.Option(None, help="Clave de sectors.yaml"),
    city: list[str] | None = typer.Option(None, help="Nombre de cities.yaml"),
    max_priority: int = 1,
    limit: int = typer.Option(0, help="Máximo de búsquedas (0 = todas)"),
) -> None:
    """F1: busca negocios en Google Maps y los guarda como NEW."""
    from leadbot.discovery.pipeline import run_discovery

    found = build_queries(sectors=sector, cities=city, max_priority=max_priority)
    stats = run_discovery(found[:limit] if limit else found)
    typer.echo(
        f"{stats.queries} búsquedas · {stats.found} resultados · {stats.new} leads nuevos · {stats.failed} fallidas"
    )


@app.command()
def enrich(limit: int = 50) -> None:
    """F1: visita la web de los leads NEW y extrae sus canales de contacto."""
    from leadbot.enrichment.pipeline import run_enrichment

    stats = run_enrichment(limit=limit)
    typer.echo(f"{stats.processed} procesados · {stats.enriched} con canales · {stats.unqualified} sin canales")


@app.command("import-xlsx")
def import_xlsx(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="Exportación de la pestaña Leads"),
) -> None:
    """Carga en Postgres los leads de un .xlsx exportado de Sheets (en vez de repetir discover y enrich)."""
    from leadbot.sheets.importer import import_xlsx as run_import

    stats = run_import(path)
    typer.echo(
        f"{stats.rows} filas · {stats.created} leads nuevos · {stats.existing} ya existían · "
        f"{stats.skipped} sin datos · {stats.contacts} canales de contacto"
    )


@app.command()
def research(company_id: int) -> None:
    """F2: solo la investigación de un lead (web, señales y búsqueda), sin IA ni escribir nada."""
    from leadbot.agents.nodes import LeadAgentNodes
    from leadbot.agents.pipeline import initial_state
    from leadbot.tools.registry import default_tools

    state, _ = initial_state(company_id)
    nodes = LeadAgentNodes(default_tools(), dry_run=True)
    lead = state["lead"]
    typer.echo(f"# {lead['name']} ({lead['city']}) · canales: {', '.join(lead['channels']) or 'ninguno'}\n")
    for node in (nodes.review_website, nodes.search_web_presence):
        for note in node(state)["research_notes"]:
            typer.echo(f"{note}\n")


@app.command()
def qualify(
    limit: int = typer.Option(20, help="Máximo de leads a procesar"),
    company_id: int | None = typer.Option(None, "--id", help="Procesar solo este lead"),
    dry_run: bool = typer.Option(False, help="Muestra la calificación y los mensajes sin guardar nada"),
) -> None:
    """F2: el agente investiga, califica y redacta mensajes para los leads ENRICHED (y rehace los REHACER)."""
    from leadbot.agents.pipeline import LeadAgent, run_agent

    if not dry_run and company_id is None:
        stats = run_agent(limit=limit)
        typer.echo(
            f"{stats.processed} procesados · {stats.qualified} califican · {stats.unqualified} no califican · "
            f"{stats.redrafted} rehechos · {stats.failed} fallidos · {stats.tokens} tokens · ${stats.cost_usd:.4f}"
        )
        return

    from leadbot.core.db import session_scope
    from leadbot.leads.repository import LeadRepository
    from leadbot.leads.status import LeadStatus

    if company_id is not None:
        ids = [company_id]
    else:
        with session_scope() as session:
            ids = [c.id for c in LeadRepository(session).fetch_by_status(LeadStatus.ENRICHED, limit=limit)]
    agent = LeadAgent(dry_run=dry_run)
    for lead_id in ids:
        final = agent.run(lead_id)
        _print_result(final)


def _print_result(final: dict) -> None:
    q = final["qualification"]
    cost = sum(u["cost_usd"] for u in final.get("usage", []))
    typer.echo(f"\n=== {final['lead']['name']} · score {q.score} · {'CALIFICA' if q.qualified else 'NO CALIFICA'}")
    typer.echo(f"{q.summary}\nRazón: {q.reason}\nCaso: {q.case_study} · canales: {[c.value for c in q.best_channels]}")
    for draft in final.get("drafts", []):
        typer.echo(f"\n--- {draft.channel.value} → {draft.recipient}")
        if draft.subject:
            typer.echo(f"Asunto: {draft.subject}")
        typer.echo(draft.body)
        if draft.flags:
            typer.echo(f"ALERTAS: {'; '.join(draft.flags)}")
    typer.echo(f"\n(costo: ${cost:.4f})")


@app.command()
def sync() -> None:
    """Sincroniza con Google Sheets: lee tus decisiones (Aprobación) y actualiza Aprobación y Leads."""
    from leadbot.core.config import get_settings
    from leadbot.sheets.sync import SheetsSync

    settings = get_settings()
    stats = SheetsSync(settings.google_service_account_file, settings.google_sheet_id).sync()
    review = stats.review
    typer.echo(
        f"Decisiones: {review.approved} aprobados · {review.redo} a rehacer · {review.discarded} descartados · "
        f"{review.edited} editados · {len(review.invalid)} inválidas"
    )
    for message_id, error in review.invalid.items():
        typer.echo(f"  mensaje {message_id}: {error}")
    typer.echo(f"{stats.pending} mensajes esperando aprobación · {stats.leads} leads en la pestaña Leads")


@app.command()
def stats() -> None:
    """Cuántos leads hay en cada estado."""
    from leadbot.core.db import session_scope
    from leadbot.leads.repository import LeadRepository

    with session_scope() as session:
        counts = LeadRepository(session).count_by_status()
    for status, count in sorted(counts.items()):
        typer.echo(f"{status:<18} {count}")
    typer.echo(f"{'TOTAL':<18} {sum(counts.values())}")


@app.command()
def send(channel: Channel, dry_run: bool = True) -> None:
    """F3: envía los emails APROBADOS, respetando config/limits.yaml. Las redes son asistidas (desde Sheets)."""
    raise NotImplementedError("F3")


@app.command()
def worker() -> None:
    """Corre el scheduler con todas las tareas periódicas."""
    from leadbot.jobs.scheduler import run_worker

    run_worker()


if __name__ == "__main__":
    app()
