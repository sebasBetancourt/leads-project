"""Decisiones de la pestaña Aprobación. Las primeras pruebas son puras; las demás usan Postgres."""

import pytest
from sqlalchemy import select

from leadbot.leads.approval import Decision, ReviewRow, apply_review, decision_error, lead_outcome, parse_decision
from leadbot.leads.models import Company, ContactChannel, Event, Message
from leadbot.leads.status import Channel, LeadStatus, MessageStatus


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("APROBADO", Decision.APROBADO),
        (" rehacer ", Decision.REHACER),
        ("Descartar", Decision.DESCARTAR),
        ("", None),
        ("SI", None),
    ],
)
def test_parse_decision(raw, expected):
    assert parse_decision(raw) == expected


def test_decision_errors():
    assert decision_error(ReviewRow(1, "", "hola", "", "")) is None
    assert "desconocida" in decision_error(ReviewRow(1, "", "hola", "OK", ""))
    assert "necesita notas" in decision_error(ReviewRow(1, "", "hola", "REHACER", " "))
    assert "vacío" in decision_error(ReviewRow(1, "", "", "APROBADO", ""))


def test_lead_outcome_rule():
    assert lead_outcome([Decision.APROBADO, Decision.REHACER]) == LeadStatus.APPROVED
    assert lead_outcome([Decision.REHACER, Decision.DESCARTAR]) == LeadStatus.DRAFTED
    assert lead_outcome([Decision.DESCARTAR, Decision.DESCARTAR]) == LeadStatus.UNQUALIFIED


@pytest.fixture
def pending(db, make_lead):
    """Un lead en PENDING_APPROVAL con un email y un WhatsApp esperando decisión."""
    lead = make_lead(LeadStatus.PENDING_APPROVAL, email="a@b.co", whatsapp="+573001234567")
    contacts = {c.channel: c for c in db.scalars(select(ContactChannel))}
    messages = []
    for channel in (Channel.EMAIL, Channel.WHATSAPP):
        body = f"Hola desde {channel.value}"
        message = Message(
            company_id=lead.id,
            contact_id=contacts[channel].id,
            channel=channel,
            body=body,
            original_body=body,
            subject="Asunto" if channel == Channel.EMAIL else None,
            status=MessageStatus.PENDING_APPROVAL,
        )
        db.add(message)
        messages.append(message)
    db.commit()
    return lead, messages


def test_edits_are_saved_with_a_diff(db, pending):
    lead, (email, _) = pending
    stats = apply_review(db, [ReviewRow(email.id, "Asunto nuevo", "Hola, versión mía", "", "")])
    db.commit()
    assert stats.edited == 1
    db.refresh(email)
    assert email.body == "Hola, versión mía" and email.original_body == "Hola desde email"
    assert email.status == MessageStatus.PENDING_APPROVAL
    event = db.scalar(select(Event).where(Event.kind == "message_edited"))
    assert event.data["subject"] == ["Asunto", "Asunto nuevo"] and "+Hola, versión mía" in event.data["diff"]


def test_lead_waits_until_every_message_is_decided(db, pending):
    lead, (email, whatsapp) = pending
    stats = apply_review(db, [ReviewRow(email.id, "Asunto", email.body, "APROBADO", "")])
    db.commit()
    assert stats.approved == 1 and stats.leads == {}
    assert db.get(Company, lead.id).status == LeadStatus.PENDING_APPROVAL

    stats = apply_review(db, [ReviewRow(whatsapp.id, "", whatsapp.body, "DESCARTAR", "")])
    db.commit()
    assert stats.leads == {lead.id: LeadStatus.APPROVED}
    db.refresh(email)
    assert email.status == MessageStatus.APPROVED and email.decision == "APROBADO"


def test_all_discarded_makes_the_lead_unqualified(db, pending):
    lead, messages = pending
    apply_review(db, [ReviewRow(m.id, m.subject or "", m.body, "DESCARTAR", "") for m in messages])
    db.commit()
    assert db.get(Company, lead.id).status == LeadStatus.UNQUALIFIED


def test_invalid_decisions_are_reported_not_applied(db, pending):
    _, (email, whatsapp) = pending
    stats = apply_review(
        db,
        [
            ReviewRow(email.id, "Asunto", email.body, "listo", ""),
            ReviewRow(whatsapp.id, "", whatsapp.body, "REHACER", ""),
        ],
    )
    db.commit()
    assert set(stats.invalid) == {email.id, whatsapp.id}
    db.refresh(email)
    assert email.status == MessageStatus.PENDING_APPROVAL


def test_review_is_idempotent(db, pending):
    lead, messages = pending
    rows = [ReviewRow(m.id, m.subject or "", m.body, "APROBADO", "") for m in messages]
    apply_review(db, rows)
    db.commit()
    stats = apply_review(db, rows)  # la siguiente sincronización vuelve a leer las mismas filas
    db.commit()
    assert (stats.approved, stats.leads) == (0, {})
    assert db.query(Event).filter(Event.kind == "message_reviewed").count() == 2
