"""Lectura de correos entrantes (función pura, sin red): de bytes de IMAP a `IncomingMail`."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime

MSGID_RE = re.compile(r"<[^<>\s]+>")


@dataclass
class IncomingMail:
    uid: int
    sender: str  # dirección, en minúsculas
    subject: str
    text: str  # texto del cuerpo (sin etiquetas HTML)
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    auto_submitted: bool = False  # respuesta automática (Auto-Submitted, Precedence, X-Autoreply...)
    is_dsn: bool = False  # informe de entrega (rebote)
    raw: str = ""  # el mensaje completo en texto: de aquí salen el estado del rebote y el Message-ID original
    date: datetime | None = None


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        content = part.get_payload(decode=True).decode("utf-8", errors="replace")
    return _html_to_text(content) if part.get_content_type() == "text/html" else content


def parse_email(uid: int, raw: bytes) -> IncomingMail:
    msg = message_from_bytes(raw, policy=policy.default)
    precedence = str(msg.get("Precedence", "")).lower()
    auto = (
        str(msg.get("Auto-Submitted", "no")).strip().lower() not in ("", "no")
        or "X-Autoreply" in msg
        or "X-Autorespond" in msg
        or precedence in ("auto_reply", "bulk", "junk")
    )
    try:
        sent_at = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        sent_at = None
    return IncomingMail(
        uid=uid,
        sender=parseaddr(str(msg.get("From", "")))[1].lower(),
        subject=str(msg.get("Subject", "")),
        text=_body_text(msg),
        message_id=(MSGID_RE.findall(str(msg.get("Message-ID", ""))) or [""])[0],
        in_reply_to=(MSGID_RE.findall(str(msg.get("In-Reply-To", ""))) or [""])[0],
        references=MSGID_RE.findall(str(msg.get("References", ""))),
        auto_submitted=auto,
        is_dsn=msg.get_content_type() == "multipart/report" and "delivery-status" in str(msg.get("Content-Type", "")),
        raw=raw.decode("utf-8", errors="replace"),
        date=sent_at,
    )
