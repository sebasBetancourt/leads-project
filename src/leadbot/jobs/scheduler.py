"""Scheduler del worker (una sola instancia, como en AgentSDR: no hay lock distribuido).

| Tarea                  | Frecuencia                                    | Fase |
|------------------------|-----------------------------------------------|------|
| discover (gosom)       | diario 6:00                                   | F1 ✅ |
| enrich                 | cada 10 min                                   | F1 ✅ |
| qualify + draft        | cada 10 min                                   | F2 ✅ |
| sheets sync            | cada 5 min (Aprobación, Leads, Control, Métricas) | F2 ✅ |
| send email             | cada 1 min (un envío por ciclo)               | F3 ✅ |
| fetch replies (IMAP)   | cada 15 min en horario de oficina, cada hora fuera | F3 ✅ |
| follow-ups             | cada 30 min (agenda, redacta y cierra secuencias) | F3 ✅ |
| health                 | cada 30 min (alertas) · cuenta de Gmail cada hora | F3 ✅ |
| tick                   | cada 1 min (latido: lo lee `leadbot worker-health`) | F3 ✅ |

Cada tarea anota su latido (jobs/heartbeat.py) y una excepción en una tarea no tumba al scheduler.
Las tareas de email solo se programan si hay GMAIL_ADDRESS y GMAIL_APP_PASSWORD.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from leadbot.core.config import Settings, get_settings
from leadbot.jobs import heartbeat

log = logging.getLogger(__name__)


def discover_job() -> None:
    from leadbot.discovery.pipeline import run_discovery
    from leadbot.discovery.query_builder import build_queries

    log.info("discover: %s", run_discovery(build_queries(max_priority=1)))


def enrich_job() -> None:
    from leadbot.enrichment.pipeline import run_enrichment

    log.info("enrich: %s", run_enrichment(limit=50))


def qualify_job() -> None:
    from leadbot.agents.pipeline import run_agent

    log.info("qualify: %s", run_agent(limit=20))


def sheets_sync_job() -> None:
    from leadbot.sheets.sync import SheetsSync

    settings = get_settings()
    stats = SheetsSync(settings.google_service_account_file, settings.google_sheet_id).sync()
    log.info("sheets sync: %s", stats)


def send_email_job() -> None:
    from leadbot.outreach.sender import run_send_cycle

    stats = run_send_cycle(max_messages=1)
    if stats.sent or stats.cancelled or stats.failed:
        log.info("send email: %s", stats)


def fetch_replies_job() -> None:
    from leadbot.outreach.replies import process_replies
    from leadbot.outreach.sender import gmail_from_settings

    stats = process_replies(gmail_from_settings())
    if stats.read:
        log.info("replies: %s", stats)
    if stats.error:
        raise RuntimeError(stats.error)


def follow_ups_job() -> None:
    from leadbot.agents.followup import run_follow_ups

    log.info("follow-ups: %s", run_follow_ups(limit=20))


def health_job() -> None:
    from leadbot.jobs.alerts import check_worker_health

    if new := check_worker_health():
        log.warning("alertas nuevas: %s", new)


def gmail_account_job() -> None:
    from leadbot.jobs.alerts import check_gmail_account

    if problems := check_gmail_account():
        raise RuntimeError("; ".join(problems))


def tick_job() -> None:
    """No hace nada: solo existe para dejar el latido que lee el healthcheck."""


def build_scheduler(settings: Settings) -> BlockingScheduler:
    """Registra las tareas según lo que haya configurado (sin Gmail no hay tareas de email)."""
    scheduler = BlockingScheduler(timezone="America/Bogota")

    def add(name: str, fn, trigger: str, **schedule) -> None:
        scheduler.add_job(
            heartbeat.tracked(name, fn),
            trigger,
            id=name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            **schedule,
        )

    add(heartbeat.TICK, tick_job, "interval", minutes=1)
    add("discover", discover_job, "cron", hour=6, minute=0)
    add("enrich", enrich_job, "interval", minutes=10)
    add("qualify", qualify_job, "interval", minutes=10)
    add("health", health_job, "interval", minutes=30)
    if settings.google_sheet_id:
        add("sheets_sync", sheets_sync_job, "interval", minutes=5)
    else:
        log.warning("Sin GOOGLE_SHEET_ID: la sincronización con Sheets queda apagada")

    if settings.gmail_address and settings.gmail_app_password:
        add("send_email", send_email_job, "interval", minutes=1)
        add("follow_ups", follow_ups_job, "interval", minutes=30)
        add("gmail_account", gmail_account_job, "interval", hours=1)
        # Respuestas: cada 15 min en horario de oficina, cada hora el resto del tiempo.
        add("fetch_replies", fetch_replies_job, "cron", day_of_week="mon-fri", hour="8-17", minute="*/15")
        scheduler.add_job(
            heartbeat.tracked("fetch_replies", fetch_replies_job),
            "cron",
            id="fetch_replies_off_hours",
            minute=0,
            hour="0-7,18-23",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            heartbeat.tracked("fetch_replies", fetch_replies_job),
            "cron",
            id="fetch_replies_weekend",
            day_of_week="sat,sun",
            hour="8-17",
            minute=0,
            max_instances=1,
            coalesce=True,
        )
    else:
        log.warning("Sin GMAIL_ADDRESS / GMAIL_APP_PASSWORD: envío de email y lectura de respuestas apagados")
    return scheduler


def run_worker() -> None:
    from leadbot.core.migrate import upgrade

    upgrade()
    scheduler = build_scheduler(get_settings())
    log.info("Worker listo: %s", sorted(job.id for job in scheduler.get_jobs()))
    scheduler.start()
