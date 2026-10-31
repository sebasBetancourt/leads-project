"""Envío de email contra Postgres, con SMTP falso y reloj simulado (necesita TEST_DATABASE_URL)."""

import smtplib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text, update

from leadbot.core.clock import to_db
from leadbot.core.config import Settings
from leadbot.leads.models import Company, Event, Job, Message, Suppression
from leadbot.leads.status import Channel, LeadStatus, MessageStatus
from leadbot.outreach import control, queue
from leadbot.outreach.channels.email_gmail import GmailChannel
from leadbot.outreach.sender import EmailSender, enqueue_approved, reconcile

from fakes import FakeMailServer

BOGOTA = ZoneInfo("America/Bogota")
TUESDAY_10AM = datetime(2026, 11, 3, 10, 0, tzinfo=BOGOTA).astimezone(UTC)


@pytest.fixture
def server():
    return FakeMailServer()


@pytest.fixture
def sender(server):
    adapter = GmailChannel(
        "ventas@brainsol.co", "clave", "Sebastian - BrainSol", smtp_factory=server.smtp, imap_factory=server.imap
    )
    return EmailSender(adapter, settings=Settings(_env_file=None))


@pytest.fixture
def approved(db, make_lead, make_message):
    lead = make_lead(LeadStatus.APPROVED, email="ventas@motosbga.com")
    return lead, make_message(lead)


def reload(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def jobs(db):
    db.expire_all()
    return list(db.scalars(select(Job).where(Job.kind == "send").order_by(Job.id)))


def make_runnable(db):
    """Adelanta run_after: los reintentos esperan minutos de verdad y las pruebas no."""
    db.execute(update(Job).values(run_after=text("now() - interval '1 minute'")))
    db.commit()


def test_enqueue_approved_queues_each_email_once(db, approved):
    _, message = approved
    assert enqueue_approved(db) == 1
    db.commit()
    assert enqueue_approved(db) == 0  # ya está QUEUED
    assert reload(db, Message, message.id).status == MessageStatus.QUEUED
    assert [j.dedup_key for j in jobs(db)] == [str(message.id)]


def test_social_messages_and_leads_that_are_not_sendable_are_not_queued(db, make_lead, make_message):
    replied = make_lead(LeadStatus.REPLIED, name="Ya respondió", email="r@a.co")
    make_message(replied)
    assert enqueue_approved(db) == 0


def test_sends_the_email_and_updates_everything(db, sender, server, approved):
    lead, message = approved
    stats = sender.run(now=TUESDAY_10AM)

    assert (stats.enqueued, stats.sent, stats.blocked) == (1, 1, "")
    (mail,) = server.sent
    assert mail["To"] == "ventas@motosbga.com" and mail["Subject"] == message.subject
    sent = reload(db, Message, message.id)
    assert sent.status == MessageStatus.SENT and sent.sent_at is not None
    assert sent.external_id == mail["Message-ID"]  # el Message-ID guardado es el que salió
    assert reload(db, Company, lead.id).status == LeadStatus.CONTACTED
    assert jobs(db)[0].status == "done"
    assert db.scalar(select(Event.id).where(Event.kind == "sent", Event.message_id == message.id)) is not None


def test_running_again_never_sends_the_same_message_twice(db, sender, server, approved):
    sender.run(now=TUESDAY_10AM)
    later = TUESDAY_10AM + timedelta(hours=2)
    stats = sender.run(now=later)
    assert stats.sent == 0 and len(server.sent) == 1


def test_dry_run_changes_nothing(db, sender, server, approved):
    _, message = approved
    stats = sender.run(dry_run=True, now=TUESDAY_10AM)
    assert stats.would_send and "ventas@motosbga.com" in stats.would_send[0]
    assert not server.sent and jobs(db) == []
    assert reload(db, Message, message.id).status == MessageStatus.APPROVED


def test_outside_the_window_nothing_is_taken_and_the_message_waits(db, sender, server, approved):
    _, message = approved
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=BOGOTA).astimezone(UTC)
    stats = sender.run(now=saturday)

    assert stats.sent == 0 and "horario" in stats.blocked
    assert stats.next_try == datetime(2026, 11, 9, 8, 0, tzinfo=BOGOTA).astimezone(UTC)  # lunes 8:00
    assert not server.sent
    job = jobs(db)[0]
    assert job.status == "pending" and job.attempts == 0  # esperar no gasta intentos
    assert reload(db, Message, message.id).status == MessageStatus.QUEUED


def test_daily_cap_follows_the_warmup(db, sender, server, make_lead, make_message):
    leads = [make_lead(LeadStatus.CONTACTED, name=f"N{i}", email=f"n{i}@x.co") for i in range(31)]
    for lead in leads[:30]:  # el calentamiento permite 30 el primer día
        make_message(lead, status=MessageStatus.SENT, sent_at=to_db(TUESDAY_10AM - timedelta(hours=1)))
    pending = make_lead(LeadStatus.APPROVED, name="Pendiente", email="p@x.co")
    make_message(pending)

    stats = sender.run(now=TUESDAY_10AM)
    assert stats.sent == 0 and "tope" in stats.blocked
    assert stats.next_try == datetime(2026, 11, 4, 8, 0, tzinfo=BOGOTA).astimezone(UTC)  # mañana 8:00


def test_cap_grows_with_the_days_the_account_has_been_active(db, sender, server, make_lead, make_message):
    for day in (1, 2):  # dos días de actividad previos: el tope pasa de 30 a 36
        for i in range(2):
            lead = make_lead(LeadStatus.CONTACTED, name=f"D{day}{i}", email=f"d{day}{i}@x.co")
            make_message(lead, status=MessageStatus.SENT, sent_at=to_db(TUESDAY_10AM - timedelta(days=day, hours=2)))
    for i in range(33):
        make_message(
            make_lead(LeadStatus.CONTACTED, name=f"H{i}", email=f"h{i}@x.co"),
            status=MessageStatus.SENT,
            sent_at=to_db(TUESDAY_10AM - timedelta(hours=1)),
        )
    make_message(make_lead(LeadStatus.APPROVED, name="Nuevo", email="nuevo@x.co"))

    assert sender.run(now=TUESDAY_10AM).sent == 1  # 33 de 36 enviados hoy


def test_pacing_between_sends_is_stable_and_respected(db, sender, server, make_lead, make_message):
    first = make_lead(LeadStatus.CONTACTED, name="Primero", email="p@x.co")
    make_message(first, status=MessageStatus.SENT, sent_at=to_db(TUESDAY_10AM - timedelta(seconds=30)))
    make_message(make_lead(LeadStatus.APPROVED, name="Segundo", email="s@x.co"))

    stats = sender.run(now=TUESDAY_10AM)
    assert stats.blocked == "pausa entre envíos" and not server.sent
    assert TUESDAY_10AM + timedelta(seconds=30) < stats.next_try <= TUESDAY_10AM + timedelta(minutes=5)
    assert sender.run(now=stats.next_try + timedelta(seconds=1)).sent == 1


@pytest.mark.parametrize("how", ["env", "sheets", "all"])
def test_a_paused_channel_sends_nothing(db, server, approved, how):
    settings = Settings(_env_file=None, pause_email=how == "env", pause_all=how == "all")
    if how == "sheets":
        control.set_flag(db, "pause_email", True)
        db.commit()
    adapter = GmailChannel("v@b.co", "x", "S", smtp_factory=server.smtp, imap_factory=server.imap)
    stats = EmailSender(adapter, settings=settings).run(now=TUESDAY_10AM)
    assert "en pausa" in stats.blocked and not server.sent


def test_suppressed_recipient_is_cancelled_not_sent(db, sender, server, approved):
    lead, message = approved
    db.add(Suppression(value="ventas@motosbga.com", reason="pidió la baja"))
    db.commit()
    stats = sender.run(now=TUESDAY_10AM)

    assert (stats.sent, stats.cancelled) == (0, 1) and not server.sent
    assert reload(db, Message, message.id).status == MessageStatus.CANCELLED
    assert reload(db, Company, lead.id).status == LeadStatus.APPROVED


@pytest.mark.parametrize("status", [LeadStatus.OPT_OUT, LeadStatus.REPLIED, LeadStatus.LOST])
def test_a_lead_that_changed_state_while_queued_is_not_contacted(db, sender, server, approved, status):
    lead, message = approved
    enqueue_approved(db)
    db.get(Company, lead.id).status = status
    db.commit()
    stats = sender.run(now=TUESDAY_10AM)
    assert stats.cancelled == 1 and not server.sent
    assert reload(db, Message, message.id).status == MessageStatus.CANCELLED


def test_temporary_errors_retry_and_then_fail_for_good(db, sender, server, approved):
    _, message = approved
    server.fail_with = smtplib.SMTPServerDisconnected("connection lost")

    first = sender.run(now=TUESDAY_10AM)
    assert (first.sent, first.retry_later, first.failed) == (0, 1, 0)
    assert reload(db, Message, message.id).status == MessageStatus.QUEUED  # sigue en cola

    for attempt in (2, 3):
        make_runnable(db)
        stats = sender.run(now=TUESDAY_10AM + timedelta(hours=attempt))
    assert stats.failed == 1
    failed = reload(db, Message, message.id)
    assert failed.status == MessageStatus.FAILED and "connection lost" in failed.error
    assert jobs(db)[0].status == "failed" and jobs(db)[0].attempts == 3


def test_a_temporary_error_that_recovers_sends_without_duplicates(db, sender, server, approved):
    _, message = approved
    server.fail_with = smtplib.SMTPServerDisconnected("connection lost")
    sender.run(now=TUESDAY_10AM)
    server.fail_with = None
    make_runnable(db)
    assert sender.run(now=TUESDAY_10AM + timedelta(hours=1)).sent == 1 and len(server.sent) == 1
    assert reload(db, Message, message.id).status == MessageStatus.SENT


def test_a_refused_recipient_is_marked_invalid_and_never_retried(db, sender, server, approved):
    lead, message = approved
    server.fail_with = smtplib.SMTPRecipientsRefused({"ventas@motosbga.com": (550, b"no such user")})
    stats = sender.run(now=TUESDAY_10AM)

    assert stats.failed == 1
    assert reload(db, Message, message.id).status == MessageStatus.FAILED
    assert message.contact.invalid
    assert db.scalar(select(Suppression.id).where(Suppression.value == "ventas@motosbga.com")) is not None


def test_bad_credentials_pause_the_channel_and_keep_the_message(db, sender, server, approved):
    _, message = approved
    server.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    sender.run(now=TUESDAY_10AM)

    assert control.pause_reason(db, sender.settings, Channel.EMAIL) == "pausa activada desde la pestaña Control"
    assert reload(db, Message, message.id).status == MessageStatus.QUEUED
    assert jobs(db)[0].status == "pending" and jobs(db)[0].attempts == 0  # espera a que se reactive el canal
    assert [a[1] for a in control.recent_alerts(db)] == ["paused:email"]


def test_daily_quota_error_pauses_without_burning_an_attempt(db, sender, server, approved):
    _, message = approved
    server.fail_with = smtplib.SMTPResponseException(550, b"5.4.5 Daily user sending quota exceeded")
    sender.run(now=TUESDAY_10AM)

    job = jobs(db)[0]
    assert job.status == "pending" and job.attempts == 0
    assert reload(db, Message, message.id).status == MessageStatus.QUEUED
    assert control.pause_reason(db, sender.settings, Channel.EMAIL)


def test_follow_up_is_sent_in_the_thread_of_the_first_email(db, sender, server, make_lead, make_message):
    lead = make_lead(LeadStatus.CONTACTED, email="ventas@motosbga.com")
    make_message(
        lead,
        status=MessageStatus.SENT,
        sent_at=to_db(TUESDAY_10AM - timedelta(days=3)),
        external_id="<primero@brainsol.co>",
    )
    follow_up = make_message(lead, step=1, subject="Re: Su tienda en línea")

    assert sender.run(now=TUESDAY_10AM).sent == 1
    (mail,) = server.sent
    assert mail["In-Reply-To"] == "<primero@brainsol.co>" and mail["References"] == "<primero@brainsol.co>"
    assert reload(db, Message, follow_up.id).status == MessageStatus.SENT
    assert reload(db, Company, lead.id).status == LeadStatus.CONTACTED


# --- Caída a mitad de un envío (ADR 0007, decisión 4) -------------------------------------------


@pytest.fixture
def interrupted(db, sender, approved):
    """El worker murió después de guardar el Message-ID y antes de anotar el resultado."""
    lead, message = approved
    enqueue_approved(db)
    db.commit()
    job = jobs(db)[0]
    message = db.get(Message, message.id)
    message.external_id = "<interrumpido@brainsol.co>"
    job.status, job.attempts = "running", 1
    job.locked_at = to_db(datetime.now(UTC) - timedelta(hours=1))
    db.commit()
    return lead, message


def test_agent_queue_recovery_never_touches_send_jobs(db, interrupted):
    assert queue.release_stale(db) == 0
    db.commit()
    assert jobs(db)[0].status == "running"


def test_an_interrupted_send_is_never_retried_by_itself(db, sender, server, interrupted):
    stats = sender.run(now=TUESDAY_10AM)

    assert stats.sent == 0 and not server.sent
    assert jobs(db)[0].status == "needs_review"
    assert any(code.startswith("send_review") for _, code, _ in control.recent_alerts(db))


def test_reconcile_finds_the_email_in_the_sent_folder(db, sender, server, interrupted):
    lead, message = interrupted
    sender.run(now=TUESDAY_10AM)
    server.sent_ids = {1: "<interrumpido@brainsol.co>"}

    stats = reconcile(sender.adapter)
    assert stats.sent == 1
    assert reload(db, Message, message.id).status == MessageStatus.SENT
    assert reload(db, Company, lead.id).status == LeadStatus.CONTACTED
    assert jobs(db)[0].status == "done"


def test_reconcile_does_not_resend_unless_a_person_confirms(db, sender, server, interrupted):
    _, message = interrupted
    sender.run(now=TUESDAY_10AM)

    assert reconcile(sender.adapter).still_in_review == 1
    assert jobs(db)[0].status == "needs_review"

    assert reconcile(sender.adapter, requeue=True).requeued == 1
    assert jobs(db)[0].status == "pending"
    assert sender.run(now=TUESDAY_10AM + timedelta(hours=1)).sent == 1
    assert reload(db, Message, message.id).external_id == "<interrumpido@brainsol.co>"  # mismo Message-ID
