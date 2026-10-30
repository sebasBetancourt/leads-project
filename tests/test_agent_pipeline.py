"""El agente contra Postgres real: cola, guardado, transiciones y volver a redactar (necesita TEST_DATABASE_URL)."""

import pytest
from sqlalchemy import select

from leadbot.agents.pipeline import LeadAgent, enqueue_ready, process_queue
from leadbot.core import llm
from leadbot.leads.approval import ReviewRow, apply_review
from leadbot.leads.models import Company, Event, Job, Message, Research, Suppression
from leadbot.leads.status import Channel, LeadStatus, MessageStatus

from fakes import FakeLLM, fake_tools, qualification


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "complete_structured", fake)
    return fake


@pytest.fixture
def agent():
    return LeadAgent(fake_tools())


def statuses(db, company_id):
    return [
        e.data["to"]
        for e in db.scalars(
            select(Event).where(Event.company_id == company_id, Event.kind == "status_changed").order_by(Event.id)
        )
    ]


def test_qualified_lead_ends_pending_approval_with_messages(db, make_lead, fake_llm, agent):
    lead = make_lead(email="ventas@motosbga.com", whatsapp="+573001234567")
    assert enqueue_ready() == 1
    stats = process_queue(agent=agent)
    assert (stats.processed, stats.qualified, stats.failed) == (1, 1, 0)
    assert stats.cost_usd == pytest.approx(0.006)

    db.expire_all()
    company = db.get(Company, lead.id)
    assert company.status == LeadStatus.PENDING_APPROVAL and company.score == 85
    assert statuses(db, lead.id) == ["QUALIFIED", "DRAFTED", "PENDING_APPROVAL"]

    research = db.scalar(select(Research).where(Research.company_id == lead.id))
    assert research.qualified and research.case_study == "tiendah2r"
    assert research.tokens == 450 and research.model == "fake/draft, fake/qualify"
    assert research.data["signals"]["copyright_year"] == 2018

    messages = db.scalars(select(Message).where(Message.company_id == lead.id)).all()
    assert {m.channel for m in messages} == {Channel.EMAIL, Channel.WHATSAPP}
    assert all(m.status == MessageStatus.PENDING_APPROVAL and m.original_body == m.body for m in messages)
    assert db.scalar(select(Job.status)) == "done"


def test_unqualified_lead(db, make_lead, fake_llm, agent):
    fake_llm.qualification = qualification(score=20)
    lead = make_lead(email="a@b.co")
    enqueue_ready()
    assert process_queue(agent=agent).unqualified == 1
    db.expire_all()
    assert db.get(Company, lead.id).status == LeadStatus.UNQUALIFIED
    assert db.scalar(select(Message.id)) is None


def test_suppressed_contacts_are_never_used(db, make_lead, fake_llm, agent):
    lead = make_lead(email="no@motosbga.com", whatsapp="+573001234567")
    db.add(Suppression(value="no@motosbga.com", reason="pidió no ser contactado"))
    db.commit()
    enqueue_ready()
    process_queue(agent=agent)
    channels = db.scalars(select(Message.channel).where(Message.company_id == lead.id)).all()
    assert channels == [Channel.WHATSAPP]


def test_running_twice_does_not_duplicate(db, make_lead, fake_llm, agent):
    make_lead(email="a@b.co")
    enqueue_ready()
    enqueue_ready()
    process_queue(agent=agent)
    assert enqueue_ready() == 0  # ya no está ENRICHED
    assert process_queue(agent=agent).processed == 0
    assert db.query(Research).count() == 1 and db.query(Message).count() == 1


def test_llm_failure_retries_later(db, make_lead, monkeypatch, agent):
    def down(*args, **kwargs):
        raise llm.LLMError("groq: RateLimitError")

    monkeypatch.setattr(llm, "complete_structured", down)
    lead = make_lead(email="a@b.co")
    enqueue_ready()
    assert process_queue(agent=agent).failed == 1
    db.expire_all()
    job = db.scalar(select(Job))
    assert job.status == "pending" and "RateLimitError" in job.last_error
    assert db.get(Company, lead.id).status == LeadStatus.ENRICHED  # nada a medias


def test_rehacer_round_trip(db, make_lead, fake_llm, agent):
    lead = make_lead(email="a@b.co", whatsapp="+573001234567")
    enqueue_ready()
    process_queue(agent=agent)
    email, whatsapp = sorted(db.scalars(select(Message)).all(), key=lambda m: m.channel.value)

    stats = apply_review(
        db,
        [
            ReviewRow(email.id, email.subject, email.body, "REHACER", "Más corto, sin tecnicismos"),
            ReviewRow(whatsapp.id, "", whatsapp.body, "DESCARTAR", ""),
        ],
    )
    db.commit()
    assert stats.leads == {lead.id: LeadStatus.DRAFTED}

    fake_llm.prompts.clear()
    assert enqueue_ready() == 1
    assert process_queue(agent=agent).redrafted == 1
    db.expire_all()
    assert db.get(Company, lead.id).status == LeadStatus.PENDING_APPROVAL
    new = db.scalars(select(Message).where(Message.status == MessageStatus.PENDING_APPROVAL)).all()
    assert [m.channel for m in new] == [Channel.EMAIL]
    assert "Más corto, sin tecnicismos" in fake_llm.prompts[0][1]
    assert db.query(Research).count() == 1  # se reutilizó la investigación
    assert db.scalar(select(Research.tokens)) == 450 + 150

    # Segunda ronda: se aprueba el nuevo; el DESCARTAR viejo no cambia el resultado.
    apply_review(db, [ReviewRow(new[0].id, new[0].subject, new[0].body, "APROBADO", "")])
    db.commit()
    db.expire_all()
    assert db.get(Company, lead.id).status == LeadStatus.APPROVED
