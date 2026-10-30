"""Cola de trabajos sobre la tabla `jobs` (patrón AgentSDR: Postgres + SKIP LOCKED, sin broker).

    SELECT * FROM jobs
    WHERE status = 'pending' AND run_after <= now()
    ORDER BY run_after
    FOR UPDATE SKIP LOCKED
    LIMIT 1

Varios workers pueden leer la misma cola sin tomar el mismo trabajo. Los envíos de un canal
se procesan de a uno, con la pausa de guardrails.next_delay_seconds entre cada uno.

Ciclo de un trabajo: pending -> running -> done | failed (o de vuelta a pending si se reintenta).
`dedup_key` evita encolar dos veces lo mismo mientras esté vivo (índice único parcial uq_jobs_alive).
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from leadbot.leads.models import Job

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=30)  # un trabajo "running" más viejo que esto: el worker murió


def enqueue(
    session: Session, kind: str, payload: dict, run_after: datetime | None = None, *, dedup_key: str | None = None
) -> Job | None:
    """Encola un trabajo. Con dedup_key, devuelve None si ya había uno vivo igual."""
    values = {"kind": kind, "payload": payload, "dedup_key": dedup_key, "status": "pending", "attempts": 0}
    if run_after is not None:
        values["run_after"] = run_after
    stmt = insert(Job).values(**values).on_conflict_do_nothing().returning(Job.id)
    job_id = session.scalar(stmt)
    return session.get(Job, job_id) if job_id is not None else None


def claim_next(session: Session, kind: str) -> Job | None:
    """Toma el siguiente trabajo listo y lo marca running. El commit de la sesión libera el candado."""
    job = session.scalar(
        select(Job)
        .where(Job.kind == kind, Job.status == "pending", Job.run_after <= func.now())
        .order_by(Job.run_after, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_at = func.now()
    session.flush()
    return job


def complete(session: Session, job: Job) -> None:
    job.status = "done"
    job.last_error = None


def fail(session: Session, job: Job, error: str, *, retryable: bool) -> None:
    """Reintenta con espera creciente hasta MAX_ATTEMPTS; después queda como 'failed'."""
    job.last_error = error[:2000]
    if retryable and job.attempts < MAX_ATTEMPTS:
        job.status = "pending"
        job.run_after = func.now() + timedelta(minutes=2**job.attempts)  # 2, 4, 8 minutos
    else:
        job.status = "failed"


def release_stale(session: Session, older_than: timedelta = STALE_AFTER) -> int:
    """Devuelve a pending los trabajos que quedaron running porque el worker se cayó."""
    result = session.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < func.now() - older_than)
        .values(status="pending", locked_at=None)
    )
    return result.rowcount
