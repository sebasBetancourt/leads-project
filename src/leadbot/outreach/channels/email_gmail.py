"""Email por Gmail: SMTP (smtp.gmail.com:465) con contraseña de aplicación. Respuestas por IMAP.

Las conexiones se abren por operación (un envío cada minuto como máximo): sin sesiones largas que se caigan.
`smtp_factory` e `imap_factory` existen para las pruebas.
"""

import html
import imaplib
import logging
import re
import smtplib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from leadbot.core.clock import utcnow
from leadbot.leads.status import Channel
from leadbot.outreach.channels.base import ChannelAdapter, OutgoingMessage, SendResult
from leadbot.outreach.channels.mailparse import IncomingMail, parse_email

log = logging.getLogger(__name__)

URL_RE = re.compile(r"(https?://[^\s<>\"]+?)([.,;:!?)]*(?:\s|$))")
QUOTA_HINTS = ("5.4.5", "daily user sending", "sending limit", "rate limit")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class FetchResult:
    mails: list[IncomingMail] = field(default_factory=list)
    uidvalidity: int | None = None
    last_uid: int = 0  # el UID más alto visto: desde ahí se sigue la próxima vez


def imap_date(moment: datetime) -> str:
    """Fecha para SEARCH SINCE (los meses van en inglés sin importar el idioma del sistema)."""
    return f"{moment.day:02d}-{MONTHS[moment.month - 1]}-{moment.year}"


def message_to_html(body: str) -> str:
    """HTML mínimo: párrafos, saltos de línea y enlaces. El texto plano sigue siendo la versión principal."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", body.strip()):
        escaped = html.escape(block).replace("\n", "<br>\n")
        paragraphs.append(
            "<p>" + URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>{m.group(2)}', escaped) + "</p>"
        )
    return "<html><body>" + "\n".join(paragraphs) + "</body></html>"


class GmailChannel(ChannelAdapter):
    channel = Channel.EMAIL

    def __init__(
        self,
        address: str,
        app_password: str,
        sender_name: str,
        *,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 465,
        imap_host: str = "imap.gmail.com",
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
        imap_factory: Callable[..., imaplib.IMAP4] | None = None,
    ):
        self.address = address
        self.app_password = app_password
        self.sender_name = sender_name
        self.smtp_host, self.smtp_port, self.imap_host = smtp_host, smtp_port, imap_host
        self._smtp_factory = smtp_factory or (lambda host, port: smtplib.SMTP_SSL(host, port, timeout=30))
        self._imap_factory = imap_factory or (lambda host: imaplib.IMAP4_SSL(host, timeout=30))

    @property
    def domain(self) -> str:
        return self.address.rpartition("@")[2] or "localhost"

    def new_message_id(self) -> str:
        return make_msgid(domain=self.domain)

    # --- Envío ---------------------------------------------------------------------------

    def build(self, message: OutgoingMessage) -> EmailMessage:
        """Texto plano + HTML mínimo, con Message-ID propio, hilo y List-Unsubscribe."""
        mail = EmailMessage()
        mail["From"] = formataddr((self.sender_name, self.address))
        mail["To"] = message.recipient
        mail["Subject"] = message.subject or ""
        mail["Date"] = formatdate(localtime=True)
        mail["Message-ID"] = message.external_id or self.new_message_id()
        if message.in_reply_to:
            mail["In-Reply-To"] = message.in_reply_to
            chain = [*dict.fromkeys([*message.references, message.in_reply_to])]  # sin repetir, en orden
            mail["References"] = " ".join(chain[-10:])
        mail["List-Unsubscribe"] = f"<mailto:{self.address}?subject=baja>"
        mail.set_content(message.body)
        mail.add_alternative(message_to_html(message.body), subtype="html")
        if message.attachment_path and Path(message.attachment_path).is_file():
            path = Path(message.attachment_path)
            mail.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
        return mail

    def send(self, message: OutgoingMessage) -> SendResult:
        mail = self.build(message)
        try:
            with self._smtp_factory(self.smtp_host, self.smtp_port) as smtp:
                smtp.login(self.address, self.app_password)
                smtp.send_message(mail)
        except smtplib.SMTPAuthenticationError as e:
            return SendResult(False, error=f"login rechazado: {_text(e)}", pause_channel=True)
        except smtplib.SMTPRecipientsRefused as e:
            return SendResult(False, error=f"destinatario rechazado: {e.recipients}", bad_recipient=True)
        except smtplib.SMTPResponseException as e:
            error = f"{e.smtp_code} {_text(e)}"
            if any(hint in error.lower() for hint in QUOTA_HINTS):
                return SendResult(False, error=error, retryable=True, pause_channel=True)  # cuota diaria de Gmail
            return SendResult(False, error=error, retryable=400 <= e.smtp_code < 500)
        except (smtplib.SMTPException, OSError) as e:  # desconexión, timeout, DNS, TLS: temporal
            return SendResult(False, error=f"{type(e).__name__}: {e}", retryable=True)
        return SendResult(True, external_id=mail["Message-ID"])

    # --- Estado de la cuenta -------------------------------------------------------------

    def check(self) -> list[str]:
        """Problemas de la cuenta (vacío = todo bien). Entra a SMTP e IMAP sin enviar nada."""
        problems = []
        try:
            with self._smtp_factory(self.smtp_host, self.smtp_port) as smtp:
                smtp.login(self.address, self.app_password)
        except (smtplib.SMTPException, OSError) as e:
            problems.append(f"SMTP: {type(e).__name__}: {e}")
        try:
            conn = self._imap_factory(self.imap_host)
            try:
                conn.login(self.address, self.app_password)
            finally:
                _logout(conn)
        except (imaplib.IMAP4.error, OSError) as e:
            problems.append(f"IMAP: {type(e).__name__}: {e}")
        return problems

    def health_check(self) -> bool:
        return not self.check()

    # --- Lectura (IMAP) ------------------------------------------------------------------

    def fetch_replies(
        self,
        after_uid: int = 0,
        uidvalidity: int | None = None,
        since_days: int = 14,
        folder: str = "INBOX",
        limit: int = 200,
    ) -> FetchResult:
        """Correos nuevos de la bandeja de entrada.

        Con cursor válido lee solo los UID mayores; sin cursor (o si el servidor renumeró) lee los últimos
        `since_days` días. Abre en solo lectura y con PEEK: no marca nada como leído.
        """
        conn = self._imap_factory(self.imap_host)
        try:
            conn.login(self.address, self.app_password)
            conn.select(folder, readonly=True)
            current = _uidvalidity(conn)
            resume = bool(after_uid) and uidvalidity is not None and uidvalidity == current
            if resume:
                _, data = conn.uid("SEARCH", None, f"UID {after_uid + 1}:*")
            else:
                since = imap_date(utcnow() - timedelta(days=since_days))
                _, data = conn.uid("SEARCH", None, "SINCE", since)
            uids = sorted(int(u) for u in (data[0] or b"").split())
            uids = [u for u in uids if not resume or u > after_uid][:limit]  # `n:*` siempre devuelve el último
            result = FetchResult(uidvalidity=current, last_uid=max([after_uid if resume else 0, *uids]))
            for uid in uids:
                _, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    result.mails.append(parse_email(uid, raw))
            return result
        finally:
            _logout(conn)

    def sent_message_ids(self, since_days: int = 3) -> set[str]:
        """Message-ID de lo que Gmail tiene en Enviados (para reconciliar un envío dudoso)."""
        conn = self._imap_factory(self.imap_host)
        try:
            conn.login(self.address, self.app_password)
            conn.select(_sent_folder(conn), readonly=True)
            _, data = conn.uid("SEARCH", None, "SINCE", imap_date(utcnow() - timedelta(days=since_days)))
            ids: set[str] = set()
            for uid in (data[0] or b"").split():
                _, parts = conn.uid("FETCH", uid.decode(), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                header = next((p[1] for p in parts if isinstance(p, tuple)), b"").decode("utf-8", errors="replace")
                ids.update(re.findall(r"<[^<>\s]+>", header))
            return ids
        finally:
            _logout(conn)


def _text(error: smtplib.SMTPResponseException) -> str:
    detail = error.smtp_error
    return detail.decode("utf-8", errors="replace") if isinstance(detail, bytes) else str(detail)


def _logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def _uidvalidity(conn: imaplib.IMAP4) -> int | None:
    _, values = conn.response("UIDVALIDITY")
    return int(values[0]) if values and values[0] else None


def _sent_folder(conn: imaplib.IMAP4) -> str:
    """La carpeta marcada \\Sent (en cuentas en español es '[Gmail]/Enviados', no 'Sent Mail')."""
    _, folders = conn.list()
    for entry in folders or []:
        line = entry.decode("utf-8", errors="replace") if isinstance(entry, bytes) else str(entry)
        if "\\Sent" in line:
            return '"' + line.rsplit(' "/" ', 1)[-1].strip().strip('"') + '"'
    return '"[Gmail]/Sent Mail"'
