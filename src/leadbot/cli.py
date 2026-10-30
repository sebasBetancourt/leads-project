"""CLI: leadbot <comando>. Cada comando es una etapa del pipeline y se puede correr sola."""

import typer

from leadbot.core.logging import setup_logging
from leadbot.discovery.query_builder import build_queries
from leadbot.leads.status import Channel

app = typer.Typer(help="BrainSol Leads: prospección automatizada para Colombia", no_args_is_help=True)


@app.callback()
def main(verbose: bool = False) -> None:
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


@app.command()
def qualify(limit: int = 20) -> None:
    """F2: el agente investiga, califica y redacta mensajes para los leads ENRICHED."""
    raise NotImplementedError("F2")


@app.command()
def sync() -> None:
    """Sincroniza Postgres -> Google Sheets (pestaña Leads)."""
    from leadbot.core.config import get_settings
    from leadbot.sheets.sync import SheetsSync

    settings = get_settings()
    rows = SheetsSync(settings.google_service_account_file, settings.google_sheet_id).push()
    typer.echo(f"{rows} leads en la pestaña Leads")


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
