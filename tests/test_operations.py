"""Piezas operativas de la F3: ventana de envío, pausas, Control, Métricas, latido, alertas y logs JSON."""

import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from leadbot.core.clock import to_db
from leadbot.core.config import Settings
from leadbot.core.logging import JsonFormatter
from leadbot.jobs import alerts, heartbeat
from leadbot.leads.metrics import daily_metrics, metrics_grid
from leadbot.leads.models import Event, Heartbeat, Job, Message
from leadbot.leads.status import Channel, MessageStatus
from leadbot.outreach import control, queue
from leadbot.outreach.guardrails import load_policy, next_window_start, pacing_delay
from leadbot.sheets.sync import control_grid, parse_control

BOGOTA = ZoneInfo("America/Bogota")


def bogota(*args) -> datetime:
    return datetime(*args, tzinfo=BOGOTA)


# --- Ventana de envío ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (bogota(2026, 11, 3, 10, 0), bogota(2026, 11, 3, 10, 0)),  # martes dentro del horario: ya
        (bogota(2026, 11, 3, 6, 0), bogota(2026, 11, 3, 8, 0)),  # martes antes de abrir
        (bogota(2026, 11, 3, 18, 0), bogota(2026, 11, 4, 8, 0)),  # martes al cerrar: mañana
        (bogota(2026, 11, 6, 19, 0), bogota(2026, 11, 9, 8, 0)),  # viernes tarde: lunes
        (bogota(2026, 11, 7, 12, 0), bogota(2026, 11, 9, 8, 0)),  # sábado: lunes
        (bogota(2026, 11, 8, 12, 0), bogota(2026, 11, 9, 8, 0)),  # domingo: lunes
    ],
)
def test_next_window_start(now, expected):
    policy = load_policy()
    assert next_window_start(policy, now.astimezone(UTC)) == expected.astimezone(UTC)


def test_pacing_delay_is_stable_per_message_and_within_limits():
    limits = load_policy().channels[Channel.EMAIL]
    assert pacing_delay(limits, 42) == pacing_delay(limits, 42)
    delays = {pacing_delay(limits, i) for i in range(50)}
    assert len(delays) > 10 and all(limits.min_delay_seconds <= d <= limits.max_delay_seconds for d in delays)


# --- Cola ---------------------------------------------------------------------------------------


def test_defer_does_not_spend_an_attempt(db):
    job = queue.enqueue(db, "send", {"message_id": 1}, dedup_key="1")
    claimed = queue.claim_next(db, "send")
    assert claimed.attempts == 1
    queue.defer(claimed)
    db.commit()
    db.expire_all()
    job = db.get(Job, job.id)
    assert (job.status, job.attempts) == ("pending", 0)


def test_quarantine_only_takes_send_jobs_and_release_stale_the_rest(db):
    old = to_db(datetime.now(UTC) - timedelta(hours=2))
    for kind in ("send", "qualify"):
        job = queue.enqueue(db, kind, {"company_id": 1}, dedup_key=kind)
        job.status, job.locked_at = "running", old
    db.commit()

    assert queue.release_stale(db) == 1  # qualify vuelve a la cola
    assert [j.kind for j in queue.quarantine_stale(db)] == ["send"]
    db.commit()
    assert {j.kind: j.status for j in db.scalars(select(Job))} == {"send": "needs_review", "qualify": "pending"}


# --- Pausas y alertas ---------------------------------------------------------------------------


def test_env_pause_wins_over_the_sheet_flag(db):
    settings = Settings(_env_file=None, pause_email=True)
    control.set_flag(db, "pause_email", False)
    assert "env" in control.pause_reason(db, settings, Channel.EMAIL)
    assert control.pause_reason(db, Settings(_env_file=None), Channel.EMAIL) is None


def test_pause_all_from_sheets_stops_every_channel(db):
    control.set_flag(db, "pause_all", True)
    settings = Settings(_env_file=None)
    assert all(control.pause_reason(db, settings, c) for c in Channel)


def test_the_same_alert_is_not_repeated_within_hours(db):
    assert control.record_alert(db, "x", "algo pasó")
    assert not control.record_alert(db, "x", "algo pasó")
    assert control.record_alert(db, "y", "otra cosa")
    assert [code for _, code, _ in control.recent_alerts(db)] == ["y", "x"]


def test_parse_control_reads_only_pause_rows_with_a_value():
    values = [
        ["clave", "valor (TRUE pausa)", "efectivo", "nota"],
        ["pause_all", "FALSE", "activo", ""],
        ["pause_email", "true", "activo", ""],
        ["pause_whatsapp", "", "activo", ""],
        ["cualquier_cosa", "TRUE", "", ""],
        ["última sincronización", "2026-11-03", "", ""],
    ]
    assert parse_control(values) == {"pause_all": False, "pause_email": True}


def test_control_grid_shows_pauses_alerts_and_the_last_sync():
    settings = Settings(_env_file=None, pause_linkedin=True)
    grid = control_grid(
        {"pause_email": "true"},
        settings,
        [("2026-11-03 10:00 UTC", "paused:email", "Canal pausado")],
        [("send_email", "2026-11-03 10:01 UTC", "ok")],
        "2026-11-03 10:02 UTC",
    )
    rows = {row[0]: row for row in grid if row}
    assert rows["pause_email"][1:3] == ["TRUE", "PAUSADO"]
    assert rows["pause_instagram"][1:3] == ["FALSE", "activo"]
    assert "forzado por .env" in rows["pause_linkedin"][3]
    assert rows["2026-11-03 10:00 UTC"][1] == "paused:email"
    assert rows["última sincronización"][1] == "2026-11-03 10:02 UTC"


# --- Latido -------------------------------------------------------------------------------------


def test_tracked_task_records_success_and_swallows_failures(db):
    heartbeat.tracked("ok_task", lambda: None)()

    def boom():
        raise RuntimeError("se cayó")

    heartbeat.tracked("bad_task", boom)()  # no lanza: el scheduler sigue vivo
    db.expire_all()
    ok, bad = db.get(Heartbeat, "ok_task"), db.get(Heartbeat, "bad_task")
    assert ok.ok and not bad.ok and "se cayó" in bad.detail


def test_worker_is_alive_only_with_a_recent_tick(db):
    assert not heartbeat.worker_alive()
    heartbeat.beat(heartbeat.TICK)
    assert heartbeat.worker_alive()
    db.execute(update(Heartbeat).values(last_run=to_db(datetime.now(UTC) - timedelta(minutes=10))))
    db.commit()
    assert not heartbeat.worker_alive()


def test_health_check_raises_alerts_for_failed_review_and_failed_tasks(db):
    for key, status in (("1", "failed"), ("2", "needs_review")):
        job = queue.enqueue(db, "send", {"message_id": int(key)}, dedup_key=key)
        job.status = status
    heartbeat.tracked("qualify", lambda: 1 / 0)()
    db.commit()

    assert set(alerts.check_worker_health()) == {"send_failed", "send_review", "task_failed:qualify"}
    assert alerts.check_worker_health() == []  # sin repetirse


# --- Métricas -----------------------------------------------------------------------------------


def test_daily_metrics_count_by_local_day_and_include_empty_days(db):
    now = bogota(2026, 11, 5, 12, 0).astimezone(UTC)
    for kind, when in (
        ("sent", now - timedelta(hours=1)),
        ("sent", now - timedelta(hours=2)),
        ("replied", now - timedelta(hours=3)),
        ("sent", now - timedelta(days=2)),
        ("bounced", now - timedelta(days=2)),
        ("sent", now - timedelta(days=90)),  # fuera de la ventana
    ):
        db.add(Event(kind=kind, data={}, created_at=to_db(when)))
    db.commit()

    metrics = daily_metrics(db, now, BOGOTA, days=7)
    assert len(metrics) == 7 and metrics[0].day.isoformat() == "2026-11-05"
    assert metrics[0].counts == {"enviados": 2, "respuestas": 1, "bajas": 0, "rebotes": 0}
    assert metrics[1].counts["enviados"] == 0
    assert metrics[2].counts["rebotes"] == 1

    grid = metrics_grid(metrics)
    assert grid[0] == ["fecha", "enviados", "respuestas", "bajas", "rebotes"]
    assert grid[-2] == ["TOTAL", 3, 1, 0, 1] and grid[-1][1] == "33.3%"


# --- Logs JSON ----------------------------------------------------------------------------------


def test_json_logs_carry_the_context_fields():
    record = logging.LogRecord(
        "leadbot.outreach.sender", logging.INFO, "f.py", 1, "email enviado a %s", ("a@b.co",), None
    )
    record.company_id, record.message_id, record.job_id = 7, 12, 3
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "email enviado a a@b.co" and entry["level"] == "INFO"
    assert (entry["company_id"], entry["message_id"], entry["job_id"]) == (7, 12, 3)


def test_sent_email_logs_include_ids(db, caplog, make_lead, make_message):
    """El log de un envío trae company_id, message_id y job_id para poder auditarlo."""
    from leadbot.leads.status import LeadStatus
    from leadbot.outreach.channels.email_gmail import GmailChannel
    from leadbot.outreach.sender import EmailSender

    from fakes import FakeMailServer

    server = FakeMailServer()
    adapter = GmailChannel("v@b.co", "x", "S", smtp_factory=server.smtp, imap_factory=server.imap)
    lead = make_lead(LeadStatus.APPROVED, email="ventas@motosbga.com")
    message = make_message(lead)
    with caplog.at_level(logging.INFO, logger="leadbot.outreach.sender"):
        EmailSender(adapter, settings=Settings(_env_file=None)).run(now=bogota(2026, 11, 3, 10, 0).astimezone(UTC))
    record = next(r for r in caplog.records if r.getMessage().startswith("email enviado"))
    assert (record.company_id, record.message_id) == (lead.id, message.id) and record.job_id
    db.expire_all()
    assert db.get(Message, message.id).status == MessageStatus.SENT
