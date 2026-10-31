"""Canal Gmail con SMTP e IMAP falsos: armado del correo, clasificación de errores y lectura del buzón."""

import smtplib

import pytest

from leadbot.outreach.channels.base import OutgoingMessage
from leadbot.outreach.channels.email_gmail import GmailChannel, imap_date, message_to_html
from leadbot.outreach.channels.mailparse import parse_email

from fakes import FakeMailServer


def channel_for(server: FakeMailServer) -> GmailChannel:
    return GmailChannel(
        "ventas@brainsol.co", "clave", "Sebastian - BrainSol", smtp_factory=server.smtp, imap_factory=server.imap
    )


def outgoing(**kw) -> OutgoingMessage:
    base = dict(
        message_id=1,
        recipient="ventas@motosbga.com",
        subject="Su tienda en línea",
        body="Hola Carlos,\n\nMire https://brainsol.vercel.app/casos.\n\nSi no te interesa, respóndeme NO.",
        external_id="<abc@brainsol.co>",
    )
    return OutgoingMessage(**(base | kw))


def test_send_builds_message_with_required_headers():
    server = FakeMailServer()
    result = channel_for(server).send(outgoing())

    assert result.ok and result.external_id == "<abc@brainsol.co>"
    (mail,) = server.sent
    assert mail["Message-ID"] == "<abc@brainsol.co>"
    assert mail["To"] == "ventas@motosbga.com"
    assert "ventas@brainsol.co" in mail["From"] and "Sebastian - BrainSol" in mail["From"]
    assert mail["List-Unsubscribe"] == "<mailto:ventas@brainsol.co?subject=baja>"
    assert mail["Subject"] == "Su tienda en línea"
    assert mail.get_body(("plain",)).get_content().startswith("Hola Carlos")
    assert '<a href="https://brainsol.vercel.app/casos">' in mail.get_body(("html",)).get_content()


def test_follow_up_goes_in_the_same_thread():
    server = FakeMailServer()
    channel_for(server).send(outgoing(in_reply_to="<b@brainsol.co>", references=["<a@brainsol.co>", "<b@brainsol.co>"]))
    (mail,) = server.sent
    assert mail["In-Reply-To"] == "<b@brainsol.co>"
    assert mail["References"] == "<a@brainsol.co> <b@brainsol.co>"


def test_attachment_is_added_when_the_file_exists(tmp_path):
    pdf = tmp_path / "propuesta.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    server = FakeMailServer()
    channel_for(server).send(outgoing(attachment_path=str(pdf)))
    names = [part.get_filename() for part in server.sent[0].iter_attachments()]
    assert names == ["propuesta.pdf"]


@pytest.mark.parametrize(
    ("error", "retryable", "pause", "bad_recipient"),
    [
        (smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted"), False, True, False),
        (smtplib.SMTPRecipientsRefused({"x@y.co": (550, b"no such user")}), False, False, True),
        (smtplib.SMTPResponseException(421, b"4.7.0 try again later"), True, False, False),
        (smtplib.SMTPResponseException(550, b"5.7.1 message rejected as spam"), False, False, False),
        (smtplib.SMTPResponseException(550, b"5.4.5 Daily user sending quota exceeded"), True, True, False),
        (smtplib.SMTPServerDisconnected("connection lost"), True, False, False),
        (TimeoutError("timed out"), True, False, False),
    ],
)
def test_send_errors_are_classified(error, retryable, pause, bad_recipient):
    result = channel_for(FakeMailServer(fail_with=error)).send(outgoing())
    assert not result.ok and result.error
    assert (result.retryable, result.pause_channel, result.bad_recipient) == (retryable, pause, bad_recipient)


def test_login_failure_is_reported_as_pause():
    server = FakeMailServer()
    server.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    result = channel_for(server).send(outgoing())
    assert result.pause_channel and not server.sent


def test_check_reports_smtp_and_imap_problems():
    server = FakeMailServer()
    assert channel_for(server).check() == []
    server.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    problems = channel_for(server).check()
    assert len(problems) == 1 and problems[0].startswith("SMTP")


def test_html_is_escaped():
    html = message_to_html("a <b> & c\n\nsegundo")
    assert "&lt;b&gt;" in html and html.count("<p>") == 2


def test_imap_date_is_locale_independent():
    from datetime import datetime

    assert imap_date(datetime(2026, 11, 3)) == "03-Nov-2026"


def mail_bytes(uid: int) -> bytes:
    return (
        f"From: Cliente <c{uid}@x.co>\r\nSubject: Re: hola\r\nMessage-ID: <m{uid}@x.co>\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\nrespuesta {uid}\r\n"
    ).encode()


def test_fetch_without_cursor_reads_recent_days_and_with_cursor_only_new_ones():
    server = FakeMailServer()
    server.inbox = {5: mail_bytes(5), 6: mail_bytes(6)}
    channel = channel_for(server)

    first = channel.fetch_replies()
    assert [m.uid for m in first.mails] == [5, 6] and first.last_uid == 6 and first.uidvalidity == 1
    assert server.searches[-1][0] == "SINCE"

    again = channel.fetch_replies(after_uid=6, uidvalidity=1)
    assert again.mails == [] and again.last_uid == 6  # `7:*` devolvió el último (6) y se descartó

    server.inbox[7] = mail_bytes(7)
    new = channel.fetch_replies(after_uid=6, uidvalidity=1)
    assert [m.uid for m in new.mails] == [7] and new.last_uid == 7


def test_fetch_rereads_recent_days_when_the_server_renumbers():
    server = FakeMailServer()
    server.inbox = {1: mail_bytes(1)}
    server.uidvalidity = 2  # el cursor guardado era de otra numeración
    result = channel_for(server).fetch_replies(after_uid=50, uidvalidity=1)
    assert [m.uid for m in result.mails] == [1] and result.uidvalidity == 2


def test_sent_message_ids_use_the_folder_flagged_sent():
    server = FakeMailServer()
    server.sent_ids = {1: "<abc@brainsol.co>", 2: "<def@brainsol.co>"}
    assert channel_for(server).sent_message_ids() == {"<abc@brainsol.co>", "<def@brainsol.co>"}


def test_parse_email_extracts_threading_and_flags():
    raw = (
        b"From: =?utf-8?q?Mar=C3=ADa?= <Maria@Tienda.CO>\r\nSubject: Respuesta autom\xc3\xa1tica\r\n"
        b"Message-ID: <x@tienda.co>\r\nIn-Reply-To: <a@brainsol.co>\r\nReferences: <z@brainsol.co> <a@brainsol.co>\r\n"
        b"Auto-Submitted: auto-replied\r\nContent-Type: text/html; charset=utf-8"
        b"\r\n\r\n<p>Fuera de la <b>oficina</b></p>"
    )
    mail = parse_email(9, raw)
    assert mail.sender == "maria@tienda.co" and mail.in_reply_to == "<a@brainsol.co>"
    assert mail.references == ["<z@brainsol.co>", "<a@brainsol.co>"]
    assert mail.auto_submitted and "Fuera de la oficina" in mail.text.replace("  ", " ")
