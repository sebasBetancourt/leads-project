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
    from leadbot.core.config import get_settings

    setup_logging("DEBUG" if verbose else "INFO", get_settings().log_format)


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
    if stats.pauses_changed:
        typer.echo(f"{stats.pauses_changed} pausas cambiadas desde la pestaña Control")
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
def send(
    channel: Channel = typer.Argument(Channel.EMAIL, help="Solo email se envía solo"),
    live: bool = typer.Option(False, "--live", help="Enviar de verdad. Sin esto solo muestra lo que saldría"),
    max_messages: int = typer.Option(
        1, "--max", help="Máximo de emails en esta corrida (el ritmo entre envíos se respeta)"
    ),
    reconcile: bool = typer.Option(
        False, "--reconcile", help="Resolver envíos que quedaron a medias, mirando Enviados"
    ),
    requeue: bool = typer.Option(False, "--requeue", help="Con --reconcile: reenviar los que NO aparecen en Enviados"),
) -> None:
    """F3: envía los emails APROBADOS respetando horario, tope diario, ritmo, pausas y lista de no contactar."""
    if channel != Channel.EMAIL:
        typer.echo("Solo el email se envía solo. Las redes son asistidas: abre el chat desde la pestaña Aprobación.")
        raise typer.Exit(1)
    from leadbot.outreach.sender import EmailSender
    from leadbot.outreach.sender import reconcile as run_reconcile

    if reconcile:
        stats = run_reconcile(_gmail(), requeue=requeue, dry_run=not live)
        typer.echo(
            f"{stats.sent} sí salieron (estaban en Enviados) · {stats.requeued} reencolados · "
            f"{stats.still_in_review} siguen en revisión{'' if live else '  (simulacro: usa --live para aplicar)'}"
        )
        if stats.still_in_review:
            typer.echo("  Si confirmas que no salieron, repite con --requeue --live.")
        return

    stats = EmailSender(_gmail()).run(max_messages, dry_run=not live)
    if not live:
        typer.echo("SIMULACRO (usa --live para enviar). Saldrían, en orden:")
        for line in stats.would_send or ["(nada aprobado en cola)"]:
            typer.echo(f"  {line}")
    else:
        typer.echo(
            f"{stats.enqueued} encolados · {stats.sent} enviados · {stats.cancelled} cancelados · "
            f"{stats.retry_later} para reintentar · {stats.failed} fallidos"
        )
    if stats.blocked:
        when = (
            f" · próximo intento posible: {stats.next_try.astimezone().strftime('%a %H:%M')}" if stats.next_try else ""
        )
        typer.echo(f"Bloqueado ahora: {stats.blocked}{when}")


@app.command()
def replies(
    dry_run: bool = typer.Option(False, help="Clasifica los correos nuevos sin aplicar nada ni mover el cursor"),
) -> None:
    """F3: lee la bandeja de entrada y aplica respuestas, bajas y rebotes."""
    from leadbot.outreach.replies import process_replies

    stats = process_replies(_gmail(), dry_run=dry_run)
    typer.echo(
        f"{stats.read} correos · {stats.replies} respuestas · {stats.opt_outs} bajas · {stats.bounces} rebotes · "
        f"{stats.auto} automáticos · {stats.unmatched} sin emparejar · {stats.duplicates} repetidos · "
        f"{stats.ignored} ignorados{'  (simulacro)' if dry_run else ''}"
    )
    if stats.error:
        typer.echo(f"ERROR: {stats.error}")
        raise typer.Exit(1)


@app.command("follow-ups")
def follow_ups(dry_run: bool = typer.Option(False, help="Solo muestra qué seguimientos tocan")) -> None:
    """F3: cierra secuencias terminadas, encola los seguimientos que tocan y los redacta para tu aprobación."""
    if dry_run:
        from leadbot.core.db import session_scope
        from leadbot.outreach.sequences import due_follow_ups

        with session_scope() as session:
            due = due_follow_ups(session)
        for item in due:
            typer.echo(f"lead {item.company_id} · paso {item.step}{' (rehacer)' if item.redo_message_id else ''}")
        typer.echo(f"{len(due)} seguimientos tocan ahora")
        return

    from leadbot.agents.followup import run_follow_ups

    stats = run_follow_ups()
    typer.echo(
        f"{stats.enqueued} encolados · {stats.processed} redactados · {stats.skipped} omitidos · "
        f"{stats.failed} fallidos · {stats.tokens} tokens · ${stats.cost_usd:.4f}"
    )


@app.command()
def health() -> None:
    """Revisa la base de datos y la cuenta de Gmail (entra a SMTP e IMAP sin enviar nada)."""
    from sqlalchemy import text

    from leadbot.core.config import get_settings
    from leadbot.core.db import session_scope
    from leadbot.outreach import control

    with session_scope() as session:
        session.execute(text("SELECT 1"))
    typer.echo("Base de datos: ok")
    problems = _gmail().check()
    typer.echo("Gmail (SMTP e IMAP): ok" if not problems else "Gmail: " + "; ".join(problems))
    with session_scope() as session:
        for channel in Channel:
            if reason := control.pause_reason(session, get_settings(), channel):
                typer.echo(f"Canal {channel.value}: PAUSADO ({reason})")
    if problems:
        raise typer.Exit(1)


@app.command("worker-health")
def worker_health() -> None:
    """Healthcheck de docker compose: falla si el worker dejó de dar señales de vida."""
    from leadbot.jobs.heartbeat import worker_alive

    if not worker_alive():
        typer.echo("El worker no ha dado señales en los últimos 5 minutos")
        raise typer.Exit(1)
    typer.echo("ok")


@app.command()
def pause(channel: Channel) -> None:
    """Pausa un canal sin reiniciar el worker (lo mismo que poner TRUE en la pestaña Control)."""
    _set_pause(channel, True)


@app.command()
def resume(channel: Channel) -> None:
    """Reactiva un canal pausado desde la pestaña Control o por una protección. No quita PAUSE_* del .env."""
    _set_pause(channel, False)


def _gmail():
    """El canal de Gmail, o un mensaje claro (sin traza) si faltan las credenciales."""
    from leadbot.outreach.sender import gmail_from_settings

    try:
        return gmail_from_settings()
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(1) from e


def _set_pause(channel: Channel, paused: bool) -> None:
    from leadbot.core.config import get_settings
    from leadbot.core.db import session_scope
    from leadbot.outreach import control

    with session_scope() as session:
        control.set_flag(session, f"pause_{channel.value}", paused)
    typer.echo(f"Canal {channel.value}: {'pausado' if paused else 'reactivado'}")
    if not paused and get_settings().is_paused(channel.value):
        typer.echo("  Ojo: el .env sigue con PAUSE_* en true y eso manda sobre este cambio.")


@app.command()
def worker() -> None:
    """Corre el scheduler con todas las tareas periódicas."""
    from leadbot.jobs.scheduler import run_worker

    run_worker()


if __name__ == "__main__":
    app()
