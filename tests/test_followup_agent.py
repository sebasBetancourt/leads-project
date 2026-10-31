"""Seguimientos de punta a punta: agenda -> agente -> aprobación -> envío -> respuesta, con reloj simulado."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from leadbot.agents.followup import reply_subject, run_follow_ups
from leadbot.core import llm
from leadbot.core.clock import to_db
from leadbot.core.config import Settings
from leadbot.leads.approval import ReviewRow, apply_review
from leadbot.leads.models import Company, Message, Research
from leadbot.leads.status import LeadStatus, MessageStatus
from leadbot.outreach.channels.email_gmail import GmailChannel
from leadbot.outreach.channels.mailparse import parse_email
from leadbot.outreach.replies import ReplyStats, handle_mail
from leadbot.outreach.sender import EmailSender

from fakes import FakeLLM, FakeMailServer, qualification

T0 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)  # martes 10:00 en Bogotá


def day(n: float) -> datetime:
    return T0 + timedelta(days=n)


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "complete_structured", fake)
    return fake


@pytest.fixture
def server():
    return FakeMailServer()


@pytest.fixture
def sender(server):
    adapter = GmailChannel(
        "ventas@brainsol.co", "clave", "Sebastian", smtp_factory=server.smtp, imap_factory=server.imap
    )
    return EmailSender(adapter, settings=Settings(_env_file=None))


@pytest.fixture
def contacted(db, make_lead, make_message):
    """Un lead al que ya se le envió el primer email, con su investigación guardada."""
    lead = make_lead(LeadStatus.CONTACTED, email="ventas@motosbga.com")
    db.add(
        Research(
            company_id=lead.id,
            summary="Repuestos para motos",
            score=85,
            qualified=True,
            case_study="tiendah2r",
            model="fake",
            data={
                "qualification": qualification().model_dump(mode="json"),
                "research_notes": ["Señales: web de 2018 sin carrito"],
                "signals": {},
            },
        )
    )
    first = make_message(
        lead,
        status=MessageStatus.SENT,
        sent_at=to_db(T0),
        external_id="<primero@brainsol.co>",
        subject="Su tienda en línea",
        body="Hola Carlos, vimos su web de repuestos.",
    )
    return lead, first


def follow_ups(db, lead):
    db.expire_all()
    return list(
        db.scalars(select(Message).where(Message.company_id == lead.id, Message.step >= 1).order_by(Message.id))
    )


def approve(db, message):
    apply_review(db, [ReviewRow(message.id, message.subject, message.body, "APROBADO", "")])
    db.commit()


def test_reply_subject():
    assert reply_subject("Su tienda en línea") == "Re: Su tienda en línea"
    assert reply_subject("Re: RE: Su tienda") == "Re: Su tienda"
    assert reply_subject(None) == "Re: seguimiento"


def test_nothing_is_drafted_before_the_follow_up_is_due(db, contacted, fake_llm):
    stats = run_follow_ups(now=day(2))
    assert (stats.enqueued, stats.processed) == (0, 0) and not fake_llm.prompts


def test_the_agent_drafts_a_follow_up_for_approval_in_the_same_thread(db, contacted, fake_llm):
    lead, _ = contacted
    stats = run_follow_ups(now=day(3))

    assert (stats.enqueued, stats.processed, stats.failed) == (1, 1, 0)
    (draft,) = follow_ups(db, lead)
    assert draft.step == 1 and draft.status == MessageStatus.PENDING_APPROVAL
    assert draft.subject == "Re: Su tienda en línea"  # lo arma el código, no el modelo
    assert "no te vuelvo a escribir" in draft.body  # la línea de baja siempre va
    assert db.get(Company, lead.id).status == LeadStatus.CONTACTED  # el lead no se mueve
    task, prompt = fake_llm.prompts[0]
    assert task == "draft" and "vimos su web de repuestos" in prompt and "ángulo nuevo" in prompt

    again = run_follow_ups(now=day(3))  # no se redacta dos veces
    assert (again.enqueued, again.processed) == (0, 0) and len(follow_ups(db, lead)) == 1


def test_the_last_step_asks_for_a_polite_close(db, contacted, make_message, fake_llm):
    lead, _ = contacted
    make_message(lead, step=1, status=MessageStatus.SENT, sent_at=to_db(day(3)))
    run_follow_ups(now=day(7))
    assert "último mensaje" in fake_llm.prompts[0][1]


def test_reviewer_notes_reach_the_prompt_when_a_follow_up_is_redone(db, contacted, fake_llm):
    lead, _ = contacted
    run_follow_ups(now=day(3))
    (draft,) = follow_ups(db, lead)
    apply_review(db, [ReviewRow(draft.id, draft.subject, draft.body, "REHACER", "menciona el catálogo de repuestos")])
    db.commit()

    stats = run_follow_ups(now=day(3))
    assert stats.processed == 1
    first, redone = follow_ups(db, lead)
    assert first.status == MessageStatus.REJECTED and redone.status == MessageStatus.PENDING_APPROVAL
    assert "menciona el catálogo de repuestos" in fake_llm.prompts[-1][1]


def test_full_sequence_sends_two_follow_ups_in_the_thread_and_then_closes_the_lead(
    db, contacted, fake_llm, sender, server
):
    lead, _ = contacted
    for n, when in ((1, day(3)), (2, day(7))):
        run_follow_ups(now=when)
        draft = follow_ups(db, lead)[-1]
        assert draft.step == n and draft.status == MessageStatus.PENDING_APPROVAL
        approve(db, draft)
        assert sender.run(now=when + timedelta(hours=1)).sent == 1

    assert [m["Subject"] for m in server.sent] == ["Re: Su tienda en línea"] * 2
    assert server.sent[0]["In-Reply-To"] == "<primero@brainsol.co>"
    second_refs = server.sent[1]["References"].split()
    assert second_refs[0] == "<primero@brainsol.co>" and len(second_refs) == 2  # primero + el seguimiento 1
    assert db.get(Company, lead.id).status == LeadStatus.CONTACTED

    assert run_follow_ups(now=day(30)).processed == 0  # no hay más pasos
    db.expire_all()
    assert db.get(Company, lead.id).status == LeadStatus.LOST


def test_a_reply_before_day_three_means_no_follow_ups_at_all(db, contacted, fake_llm, sender, server):
    lead, _ = contacted
    reply = parse_email(
        1,
        b"From: Carlos <ventas@motosbga.com>\r\nSubject: Re: Su tienda en l\xc3\xadnea\r\n"
        b"In-Reply-To: <primero@brainsol.co>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nHola, me interesa. Llamame.\r\n",
    )
    handle_mail(db, reply, day(2), "ventas@brainsol.co", ReplyStats())
    db.commit()

    assert run_follow_ups(now=day(3)).processed == 0 and run_follow_ups(now=day(10)).processed == 0
    assert db.get(Company, lead.id).status == LeadStatus.REPLIED
    assert not server.sent and not fake_llm.prompts


def test_a_reply_after_a_follow_up_was_drafted_cancels_it_before_it_goes_out(db, contacted, fake_llm, sender, server):
    lead, _ = contacted
    run_follow_ups(now=day(3))
    (draft,) = follow_ups(db, lead)
    approve(db, draft)

    reply = parse_email(
        1,
        b"From: Carlos <ventas@motosbga.com>\r\nSubject: Re: Su tienda en l\xc3\xadnea\r\n"
        b"In-Reply-To: <primero@brainsol.co>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nNo me interesa, gracias.\r\n",
    )
    handle_mail(db, reply, day(3), "ventas@brainsol.co", ReplyStats())
    db.commit()

    assert sender.run(now=day(3) + timedelta(hours=1)).sent == 0 and not server.sent
    assert follow_ups(db, lead)[0].status == MessageStatus.CANCELLED
    assert db.get(Company, lead.id).status == LeadStatus.OPT_OUT
