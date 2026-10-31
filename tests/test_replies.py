"""Respuestas y rebotes de email contra Postgres, con IMAP falso (necesita TEST_DATABASE_URL)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from leadbot.core.clock import to_db
from leadbot.core.config import Settings
from leadbot.leads.models import Company, ContactChannel, Event, MailCursor, Message, Suppression
from leadbot.leads.status import Channel, LeadStatus, MessageStatus
from leadbot.outreach import control
from leadbot.outreach.channels.email_gmail import GmailChannel
from leadbot.outreach.channels.mailparse import parse_email
from leadbot.outreach.replies import ReplyStats, handle_mail, process_replies

from fakes import FakeMailServer

FIXTURES = Path(__file__).parent / "fixtures" / "mail"
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def eml(name: str, **replace: str):
    raw = (FIXTURES / name).read_bytes().decode("utf-8")  # conserva los

    for old, new in replace.items():
        raw = raw.replace(old, new)
    return parse_email(1, raw.encode("utf-8"))


@pytest.fixture
def contacted(db, make_lead, make_message):
    """Un lead al que ya se le escribió (paso 0) y que tiene un seguimiento esperando aprobación."""
    lead = make_lead(LeadStatus.CONTACTED, email="ventas@motosbga.com", whatsapp="+573001234567")
    first = make_message(
        lead, status=MessageStatus.SENT, sent_at=to_db(NOW - timedelta(days=3)), external_id="<orig1@brainsol.co>"
    )
    follow_up = make_message(lead, step=1, status=MessageStatus.PENDING_APPROVAL, subject="Re: Su tienda en línea")
    return lead, first, follow_up


def run(db, mail, *, own="ventas@brainsol.co", dry_run=False) -> ReplyStats:
    stats = ReplyStats()
    handle_mail(db, mail, NOW, own, stats, dry_run=dry_run)
    db.commit()
    db.expire_all()
    return stats


def test_a_reply_marks_the_lead_replied_and_cancels_what_was_pending(db, contacted):
    lead, first, follow_up = contacted
    stats = run(
        db,
        eml(
            "interested_outlook_toppost.eml", **{"In-Reply-To: <orig1@brainsol.co>": "In-Reply-To: <orig1@brainsol.co>"}
        ),
    )

    assert stats.replies == 1
    assert db.get(Company, lead.id).status == LeadStatus.REPLIED
    assert db.get(Message, first.id).reply_at is not None
    assert db.get(Message, follow_up.id).status == MessageStatus.CANCELLED
    event = db.scalar(select(Event).where(Event.kind == "replied"))
    assert "me interesa" in event.data["text"] and "respóndeme NO" not in event.data["text"]  # sin lo citado


def test_the_same_email_processed_twice_has_a_single_effect(db, contacted):
    mail = eml("interested_outlook_toppost.eml")
    run(db, mail)
    again = run(db, mail)
    assert again.duplicates == 1 and again.replies == 0
    assert len(db.scalars(select(Event).where(Event.kind == "replied")).all()) == 1


def test_opt_out_is_final_and_blocks_every_contact_of_the_lead(db, contacted):
    lead, _, follow_up = contacted
    stats = run(db, eml("opt_out_no.eml"))

    assert stats.opt_outs == 1
    assert db.get(Company, lead.id).status == LeadStatus.OPT_OUT
    assert db.get(Message, follow_up.id).status == MessageStatus.CANCELLED
    blocked = set(db.scalars(select(Suppression.value)))
    assert {"ventas@motosbga.com", "+573001234567"} <= blocked  # el correo y también el WhatsApp
    assert db.scalar(select(Event.id).where(Event.kind == "opted_out")) is not None
    assert run(db, eml("opt_out_no.eml")).duplicates == 1


def test_a_hard_bounce_invalidates_the_address_and_closes_the_lead(db, contacted):
    lead, first, follow_up = contacted
    # Mientras no haya otro canal con mensajes, el lead no tiene a quién más escribirle.
    db.query(ContactChannel).filter_by(channel=Channel.WHATSAPP).delete()
    db.commit()
    stats = run(db, eml("bounce_hard.eml"))

    assert stats.bounces == 1
    assert db.scalar(select(ContactChannel.invalid).where(ContactChannel.channel == Channel.EMAIL)) is True
    assert "ventas@motosbga.com" in set(db.scalars(select(Suppression.value)))
    assert db.get(Message, follow_up.id).status == MessageStatus.CANCELLED
    assert db.get(Message, first.id).status == MessageStatus.SENT  # el envío sí ocurrió
    assert "rebotó" in db.get(Message, first.id).error
    assert db.get(Company, lead.id).status == LeadStatus.LOST


def test_a_bounce_does_not_close_a_lead_that_still_has_another_channel_in_play(db, contacted):
    lead, _, _ = contacted
    whatsapp = db.scalar(select(ContactChannel).where(ContactChannel.channel == Channel.WHATSAPP))
    db.add(
        Message(
            company_id=lead.id,
            contact_id=whatsapp.id,
            channel=Channel.WHATSAPP,
            body="Hola",
            status=MessageStatus.APPROVED,
        )
    )
    db.commit()
    run(db, eml("bounce_hard.eml"))
    assert db.get(Company, lead.id).status == LeadStatus.CONTACTED


@pytest.mark.parametrize("name", ["bounce_soft.eml", "auto_reply.eml"])
def test_soft_bounces_and_auto_replies_only_postpone(db, contacted, name):
    lead, _, follow_up = contacted
    stats = run(db, eml(name))

    assert stats.auto == 1
    assert db.get(Company, lead.id).status == LeadStatus.CONTACTED
    assert db.get(Message, follow_up.id).status == MessageStatus.PENDING_APPROVAL  # no se cancela
    assert db.scalar(select(Event.id).where(Event.kind == "auto_reply")) is not None
    assert not db.scalars(select(Suppression.id)).all()


def test_matches_by_sender_address_when_the_headers_are_lost(db, contacted):
    lead, first, _ = contacted
    mail = eml(
        "opt_out_no.eml", **{"In-Reply-To: <orig1@brainsol.co>\r\n": "", "References: <orig1@brainsol.co>\r\n": ""}
    )
    assert not mail.in_reply_to
    assert run(db, mail).opt_outs == 1
    assert db.get(Company, lead.id).status == LeadStatus.OPT_OUT


def test_matches_another_address_of_the_same_company_domain(db, contacted):
    lead, _, _ = contacted
    mail = eml(
        "opt_out_no.eml",
        **{
            "ventas@motosbga.com": "gerencia@motosbga.com",
            "In-Reply-To: <orig1@brainsol.co>\r\n": "",
            "References: <orig1@brainsol.co>\r\n": "",
        },
    )
    assert run(db, mail).opt_outs == 1
    assert db.get(Company, lead.id).status == LeadStatus.OPT_OUT


def test_free_mail_domains_are_never_matched_by_domain(db, make_lead, make_message):
    lead = make_lead(LeadStatus.CONTACTED, email="ana@gmail.com")
    make_message(lead, status=MessageStatus.SENT, sent_at=to_db(NOW - timedelta(days=3)))
    other = eml(
        "opt_out_no.eml",
        **{
            "ventas@motosbga.com": "pedro@gmail.com",
            "In-Reply-To: <orig1@brainsol.co>\r\n": "",
            "References: <orig1@brainsol.co>\r\n": "",
        },
    )
    stats = run(db, other)
    assert stats.opt_outs == 0 and db.get(Company, lead.id).status == LeadStatus.CONTACTED


def test_unmatched_reply_to_one_of_our_subjects_is_recorded_for_manual_review(db, contacted):
    mail = eml(
        "opt_out_no.eml",
        **{
            "ventas@motosbga.com": "otra@desconocida.com",
            "In-Reply-To: <orig1@brainsol.co>\r\n": "",
            "References: <orig1@brainsol.co>\r\n": "",
        },
    )
    stats = run(db, mail)
    assert stats.unmatched == 1
    assert db.scalar(select(Event.data).where(Event.kind == "reply_unmatched"))["from"] == "otra@desconocida.com"


def test_unrelated_mail_is_ignored(db, contacted):
    mail = eml(
        "opt_out_no.eml",
        **{
            "ventas@motosbga.com": "news@tienda.com",
            "Subject: Re: Su tienda en línea": "Subject: Oferta",
            "In-Reply-To: <orig1@brainsol.co>\r\n": "",
            "References: <orig1@brainsol.co>\r\n": "",
        },
    )
    stats = run(db, mail)
    assert stats.ignored == 1 and not db.scalars(select(Event).where(Event.kind == "reply_unmatched")).all()


def test_our_own_mail_is_ignored(db, contacted):
    assert run(db, eml("opt_out_no.eml"), own="ventas@motosbga.com").ignored == 1


def test_dry_run_classifies_but_changes_nothing(db, contacted):
    lead, _, _ = contacted
    assert run(db, eml("opt_out_no.eml"), dry_run=True).opt_outs == 1
    assert db.get(Company, lead.id).status == LeadStatus.CONTACTED


# --- Lectura del buzón con cursor ---------------------------------------------------------------


def adapter_for(server: FakeMailServer) -> GmailChannel:
    return GmailChannel("ventas@brainsol.co", "clave", "S", smtp_factory=server.smtp, imap_factory=server.imap)


def test_process_replies_reads_each_mail_once_and_saves_the_cursor(db, contacted):
    lead, _, _ = contacted
    server = FakeMailServer()
    server.inbox = {10: (FIXTURES / "auto_reply.eml").read_bytes(), 11: (FIXTURES / "opt_out_no.eml").read_bytes()}
    adapter = adapter_for(server)

    stats = process_replies(adapter, now=NOW)
    assert (stats.read, stats.auto, stats.opt_outs) == (2, 1, 1)
    db.expire_all()
    assert db.get(MailCursor, "INBOX").last_uid == 11 and db.get(Company, lead.id).status == LeadStatus.OPT_OUT

    again = process_replies(adapter, now=NOW)
    assert again.read == 0  # nada nuevo desde el cursor


def test_a_mail_that_fails_stops_the_cursor_so_it_is_retried(db, contacted, monkeypatch):
    server = FakeMailServer()
    server.inbox = {10: (FIXTURES / "auto_reply.eml").read_bytes(), 11: (FIXTURES / "opt_out_no.eml").read_bytes()}

    def boom(*args, **kwargs):
        if args[1].uid == 11:
            raise RuntimeError("base de datos caída")
        return real(*args, **kwargs)

    from leadbot.outreach import replies

    real = replies.handle_mail
    monkeypatch.setattr(replies, "handle_mail", boom)
    stats = process_replies(adapter_for(server), now=NOW)

    assert "11" in stats.error
    db.expire_all()
    assert db.get(MailCursor, "INBOX").last_uid == 10  # el 11 se vuelve a intentar la próxima vez


def test_dry_run_does_not_advance_the_cursor(db, contacted):
    server = FakeMailServer()
    server.inbox = {10: (FIXTURES / "opt_out_no.eml").read_bytes()}
    process_replies(adapter_for(server), dry_run=True, now=NOW)
    assert db.get(MailCursor, "INBOX") is None


def test_too_many_bounces_pause_the_channel(db, make_lead, make_message):
    now = datetime.now(UTC)  # los eventos se fechan con el reloj de Postgres: aquí no se simula el tiempo
    server = FakeMailServer()
    for i in range(4):  # el máximo tolerado son 3 en 24 horas
        lead = make_lead(LeadStatus.CONTACTED, name=f"R{i}", email=f"r{i}@negocio{i}.co")
        make_message(
            lead, status=MessageStatus.SENT, sent_at=to_db(now - timedelta(hours=1)), external_id=f"<id{i}@brainsol.co>"
        )
        raw = (FIXTURES / "bounce_hard.eml").read_bytes().decode("utf-8").replace("orig1@", f"id{i}@")
        server.inbox[i + 1] = raw.encode("utf-8")

    stats = process_replies(adapter_for(server), now=now)
    assert stats.bounces == 4
    assert (
        control.pause_reason(db, Settings(_env_file=None), Channel.EMAIL) == "pausa activada desde la pestaña Control"
    )
