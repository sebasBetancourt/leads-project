"""Latido del worker: cada tarea anota cuándo corrió y si salió bien.

`leadbot worker-health` (el healthcheck de docker compose) falla si el worker dejó de dar señales, y
`all_heartbeats` alimenta la pestaña Control.
"""

import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select

from leadbot.core.clock import from_db, to_db, utcnow
from leadbot.core.db import session_scope
from leadbot.leads.models import Heartbeat

log = logging.getLogger(__name__)

TICK = "tick"  # lo escribe una tarea cada minuto: si falta, el scheduler está muerto


def beat(task: str, ok: bool = True, detail: str | None = None) -> None:
    with session_scope() as session:
        row = session.get(Heartbeat, task)
        if row is None:
            session.add(Heartbeat(task=task, last_run=to_db(utcnow()), ok=ok, detail=detail))
        else:
            row.last_run, row.ok, row.detail = to_db(utcnow()), ok, detail


def _safe_beat(task: str, ok: bool, detail: str | None = None) -> None:
    try:
        beat(task, ok, detail)
    except Exception:
        log.exception("no se pudo anotar el latido de %s", task)


def tracked(name: str, fn: Callable[[], object]) -> Callable[[], None]:
    """Envuelve una tarea: anota el latido y no deja que una excepción mate la tarea programada."""

    def run() -> None:
        try:
            fn()
        except Exception as e:
            log.exception("tarea %s falló", name)
            _safe_beat(name, False, f"{type(e).__name__}: {e}"[:500])
        else:
            _safe_beat(name, True)

    run.__name__ = f"tracked_{name}"
    return run


def age_of(task: str) -> timedelta | None:
    with session_scope() as session:
        row = session.get(Heartbeat, task)
        return None if row is None else utcnow() - from_db(row.last_run)


def worker_alive(max_age: timedelta = timedelta(minutes=5)) -> bool:
    age = age_of(TICK)
    return age is not None and age <= max_age


def all_heartbeats() -> list[Heartbeat]:
    with session_scope() as session:
        return list(session.scalars(select(Heartbeat).order_by(Heartbeat.task)))
