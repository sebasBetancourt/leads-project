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
def discover(max_priority: int = 1) -> None:
    """F1: busca negocios en Google Maps y los guarda como NEW."""
    raise NotImplementedError("F1")


@app.command()
def enrich(limit: int = 50) -> None:
    """F1: visita la web de los leads NEW y extrae sus canales de contacto."""
    raise NotImplementedError("F1")


@app.command()
def qualify(limit: int = 20) -> None:
    """F2: el agente investiga, califica y redacta mensajes para los leads ENRICHED."""
    raise NotImplementedError("F2")


@app.command()
def sync() -> None:
    """Sincroniza Postgres <-> Google Sheets."""
    raise NotImplementedError("F1")


@app.command()
def send(channel: Channel, dry_run: bool = True) -> None:
    """F3/F4: envía los mensajes APROBADOS de un canal, respetando config/limits.yaml."""
    raise NotImplementedError("F3")


@app.command()
def login(channel: Channel) -> None:
    """F4: abre el navegador para que inicies sesión una vez en esa red."""
    raise NotImplementedError("F4")


@app.command()
def worker() -> None:
    """Corre el scheduler con todas las tareas periódicas."""
    from leadbot.jobs.scheduler import run_worker

    run_worker()


if __name__ == "__main__":
    app()
