"""Cola de trabajos sobre la tabla `jobs` (patrón AgentSDR: Postgres + SKIP LOCKED, sin broker).

    SELECT * FROM jobs
    WHERE status = 'pending' AND run_after <= now()
    ORDER BY run_after
    FOR UPDATE SKIP LOCKED
    LIMIT 1

Varios workers pueden leer la misma cola sin tomar el mismo trabajo. Los envíos de un canal
se procesan de a uno, con la pausa de guardrails.next_delay_seconds entre cada uno.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from leadbot.leads.models import Job

MAX_ATTEMPTS = 3


def enqueue(session: Session, kind: str, payload: dict, run_after: datetime | None = None) -> Job:
    raise NotImplementedError("F1")


def claim_next(session: Session, kind: str) -> Job | None:
    raise NotImplementedError("F1")


def complete(session: Session, job: Job) -> None:
    raise NotImplementedError("F1")


def fail(session: Session, job: Job, error: str, *, retryable: bool) -> None:
    """Reintenta con espera creciente hasta MAX_ATTEMPTS; después queda como 'failed'."""
    raise NotImplementedError("F1")
