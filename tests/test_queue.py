"""Cola de trabajos sobre Postgres (necesita TEST_DATABASE_URL)."""

from sqlalchemy import text

from leadbot.core.db import SessionLocal
from leadbot.leads.models import Job
from leadbot.outreach import queue


def test_enqueue_deduplicates_live_jobs(db):
    assert queue.enqueue(db, "qualify", {"company_id": 1}, dedup_key="1") is not None
    assert queue.enqueue(db, "qualify", {"company_id": 1}, dedup_key="1") is None
    assert queue.enqueue(db, "redraft", {"company_id": 1}, dedup_key="1") is not None  # otro tipo
    db.commit()
    assert db.query(Job).count() == 2


def test_done_job_can_be_enqueued_again(db):
    job = queue.enqueue(db, "qualify", {"company_id": 1}, dedup_key="1")
    queue.complete(db, job)
    db.commit()
    assert queue.enqueue(db, "qualify", {"company_id": 1}, dedup_key="1") is not None


def test_two_workers_never_claim_the_same_job(db):
    for i in range(2):
        queue.enqueue(db, "qualify", {"company_id": i}, dedup_key=str(i))
    db.commit()

    first, second = SessionLocal(), SessionLocal()
    try:
        a = queue.claim_next(first, "qualify")  # transacción abierta: la fila queda bloqueada
        b = queue.claim_next(second, "qualify")
        assert a is not None and b is not None and a.id != b.id
        assert queue.claim_next(second, "qualify") is None
        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()


def test_failed_job_is_retried_later_then_marked_failed(db):
    job = queue.enqueue(db, "qualify", {"company_id": 1}, dedup_key="1")
    db.commit()

    for attempt in range(1, queue.MAX_ATTEMPTS + 1):
        db.execute(text("UPDATE jobs SET run_after = now() - interval '1 second'"))
        claimed = queue.claim_next(db, "qualify")
        assert claimed.id == job.id and claimed.attempts == attempt
        queue.fail(db, claimed, "429 rate limit", retryable=True)
        db.commit()
        db.refresh(claimed)
        expected = "failed" if attempt == queue.MAX_ATTEMPTS else "pending"
        assert claimed.status == expected
        if expected == "pending":
            assert queue.claim_next(db, "qualify") is None  # espera antes de reintentar
    assert claimed.last_error == "429 rate limit"


def test_non_retryable_error_fails_at_once(db):
    queue.enqueue(db, "qualify", {"company_id": 1}, dedup_key="1")
    db.commit()
    job = queue.claim_next(db, "qualify")
    queue.fail(db, job, "lead no existe", retryable=False)
    db.commit()
    db.refresh(job)
    assert job.status == "failed"


def test_stale_running_jobs_are_released(db):
    queue.enqueue(db, "qualify", {"company_id": 1}, dedup_key="1")
    db.commit()
    queue.claim_next(db, "qualify")
    db.commit()
    db.execute(text("UPDATE jobs SET locked_at = now() - interval '2 hours'"))
    assert queue.release_stale(db) == 1
    db.commit()
    assert queue.claim_next(db, "qualify") is not None
