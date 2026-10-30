"""Scheduler del worker (una sola instancia, como en AgentSDR: no hay lock distribuido).

| Tarea                  | Frecuencia     | Fase |
|------------------------|----------------|------|
| discover (gosom)       | diario 6:00    | F1 ✅ |
| enrich                 | cada 10 min    | F1 ✅ |
| qualify + draft        | cada 10 min    | F2 ✅ |
| sheets sync            | cada 5 min     | F2 ✅ |  pull_decisions -> Aprobación -> Leads
| send email             | cada 1 min     | F3   |
| fetch replies (IMAP)   | cada 15 min    | F3   |
| follow-ups             | diario 7:00    | F3   |
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from leadbot.core.config import get_settings

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


def run_worker() -> None:
    from leadbot.core.migrate import upgrade

    upgrade()
    settings = get_settings()
    scheduler = BlockingScheduler(timezone="America/Bogota")
    scheduler.add_job(discover_job, "cron", hour=6, minute=0, id="discover", max_instances=1)
    scheduler.add_job(enrich_job, "interval", minutes=10, id="enrich", max_instances=1)
    scheduler.add_job(qualify_job, "interval", minutes=10, id="qualify", max_instances=1)
    if settings.google_sheet_id:
        scheduler.add_job(sheets_sync_job, "interval", minutes=5, id="sheets_sync", max_instances=1)
    else:
        log.warning("Sin GOOGLE_SHEET_ID: la sincronización con Sheets queda apagada")
    log.info("Worker listo: %s", [job.id for job in scheduler.get_jobs()])
    scheduler.start()
