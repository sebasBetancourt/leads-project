"""Cuándo toca un seguimiento y cuándo se cierra una secuencia, con reloj simulado (necesita TEST_DATABASE_URL)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from leadbot.core.clock import to_db
from leadbot.leads.models import Company, ContactChannel, Event, Job, Message, Suppression
from leadbot.leads.status import LeadStatus, MessageStatus
from leadbot.outreach import sequences
from leadbot.outreach.sequences import DueFollowUp, cancel_pending_for_company, close_finished_sequences

T0 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)  # martes 10:00 en Bogotá


def day(n: float) -> datetime:
    return T0 + timedelta(days=n)


@pytest.fixture
def contacted(db, make_lead, make_message):
    lead = make_lead(LeadStatus.CONTACTED, email="ventas@motosbga.com")
    first = make_message(lead, status=MessageStatus.SENT, sent_at=to_db(T0), external_id="<a@brainsol.co>")
    contact_id = db.scalar(select(ContactChannel.id).where(ContactChannel.company_id == lead.id))
    return lead, first, contact_id


def due(db, now):
    return sequences.due_follow_ups(db, now)


def test_first_follow_up_is_due_on_day_three(db, contacted):
    lead, _, contact_id = contacted
    assert due(db, day(2.9)) == []
    assert due(db, day(3)) == [DueFollowUp(lead.id, contact_id, 1)]


def test_second_follow_up_counts_from_the_first_email_not_from_the_previous_follow_up(db, contacted, make_message):
    lead, _, contact_id = contacted
    make_message(lead, step=1, status=MessageStatus.SENT, sent_at=to_db(day(3.1)))
    assert due(db, day(6.9)) == []
    assert due(db, day(7)) == [DueFollowUp(lead.id, contact_id, 2)]


def test_a_late_follow_up_still_leaves_a_day_before_the_next_one(db, contacted, make_message):
    lead, _, contact_id = contacted
    make_message(lead, step=1, status=MessageStatus.SENT, sent_at=to_db(day(6.5)))
    assert due(db, day(7.4)) == []
    assert due(db, day(7.5)) == [DueFollowUp(lead.id, contact_id, 2)]


def test_there_are_only_as_many_follow_ups_as_configured(db, contacted, make_message):
    lead, _, _ = contacted
    make_message(lead, step=1, status=MessageStatus.SENT, sent_at=to_db(day(3)))
    make_message(lead, step=2, status=MessageStatus.SENT, sent_at=to_db(day(7)))
    assert due(db, day(60)) == []


def test_a_follow_up_in_flight_blocks_the_next_one(db, contacted, make_message):
    lead, _, _ = contacted
    make_message(lead, step=1, status=MessageStatus.PENDING_APPROVAL)
    assert due(db, day(60)) == []


@pytest.mark.parametrize("status", [LeadStatus.REPLIED, LeadStatus.OPT_OUT, LeadStatus.LOST, LeadStatus.MEETING])
def test_leads_that_answered_or_left_get_nothing(db, contacted, status):
    lead, _, _ = contacted
    db.get(Company, lead.id).status = status
    db.commit()
    assert due(db, day(10)) == []


def test_invalid_or_suppressed_addresses_get_nothing(db, contacted):
    _, _, contact_id = contacted
    db.get(ContactChannel, contact_id).invalid = True
    db.commit()
    assert due(db, day(10)) == []
    db.get(ContactChannel, contact_id).invalid = False
    db.add(Suppression(value="ventas@motosbga.com", reason="baja"))
    db.commit()
    assert due(db, day(10)) == []


def test_an_auto_reply_postpones_the_follow_up_two_days(db, contacted):
    lead, _, contact_id = contacted
    db.add(Event(company_id=lead.id, kind="auto_reply", data={}, created_at=to_db(day(3))))
    db.commit()
    assert due(db, day(4.9)) == []
    assert due(db, day(5)) == [DueFollowUp(lead.id, contact_id, 1)]


def test_a_follow_up_sent_back_with_notes_is_due_again_until_a_newer_one_exists(db, contacted, make_message):
    lead, _, contact_id = contacted
    redo = make_message(lead, step=1, status=MessageStatus.REJECTED, decision="REHACER", reviewer_notes="más corto")
    assert due(db, day(3)) == [DueFollowUp(lead.id, contact_id, 1, redo_message_id=redo.id)]
    make_message(lead, step=1, status=MessageStatus.PENDING_APPROVAL)
    assert due(db, day(3)) == []


def test_a_discarded_follow_up_ends_the_sequence(db, contacted, make_message):
    lead, _, _ = contacted
    make_message(lead, step=1, status=MessageStatus.REJECTED, decision="DESCARTAR")
    assert due(db, day(60)) == []


def test_scheduling_enqueues_each_follow_up_once(db, contacted):
    assert sequences.schedule_follow_ups(db, day(3)) == 1
    db.commit()
    assert sequences.schedule_follow_ups(db, day(3)) == 0
    (job,) = db.scalars(select(Job).where(Job.kind == "followup"))
    assert job.payload["step"] == 1 and job.dedup_key.endswith(":1")


# --- Cierre de la secuencia ---------------------------------------------------------------------


@pytest.fixture
def finished(db, contacted, make_message):
    lead, _, _ = contacted
    make_message(lead, step=1, status=MessageStatus.SENT, sent_at=to_db(day(3)))
    make_message(lead, step=2, status=MessageStatus.SENT, sent_at=to_db(day(7)))
    return lead


def test_a_lead_that_never_answers_is_closed_after_the_last_step(db, finished):
    assert close_finished_sequences(db, day(13.9)) == 0
    assert close_finished_sequences(db, day(14)) == 1
    db.commit()
    db.expire_all()
    assert db.get(Company, finished.id).status == LeadStatus.LOST
    event = db.scalars(select(Event).where(Event.kind == "status_changed").order_by(Event.id.desc())).first()
    assert event.data["reason"] == "sin_respuesta"


def test_a_lead_is_not_closed_while_the_sequence_has_steps_left(db, contacted):
    assert close_finished_sequences(db, day(60)) == 0


def test_a_discarded_follow_up_closes_the_lead_after_the_waiting_period(db, contacted, make_message):
    lead, _, _ = contacted
    make_message(lead, step=1, status=MessageStatus.REJECTED, decision="DESCARTAR")
    assert close_finished_sequences(db, day(7)) == 1


def test_a_lead_with_something_still_in_play_is_not_closed(db, finished, make_message):
    make_message(finished, step=3, status=MessageStatus.PENDING_APPROVAL)
    assert close_finished_sequences(db, day(60)) == 0


def test_cancel_pending_for_company(db, contacted, make_message):
    lead, _, _ = contacted
    pending = make_message(lead, step=1, status=MessageStatus.PENDING_APPROVAL)
    queued = make_message(lead, step=2, status=MessageStatus.QUEUED)
    assert cancel_pending_for_company(db, lead.id, "respondió") == 2
    db.commit()
    db.expire_all()
    assert {db.get(Message, m.id).status for m in (pending, queued)} == {MessageStatus.CANCELLED}
