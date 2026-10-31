"""Respuestas por email (F3): del buzón al estado del lead. ADR 0007.

    IMAP (UID > cursor) ─► classify (reglas, sin LLM) ─► emparejar con el mensaje enviado ─► efecto

    BOUNCE        rebote definitivo: contacto inválido + no contactar + se cancelan sus pendientes
    SOFT_BOUNCE   rebote temporal (buzón lleno...) ─┐ no cambian el estado; el seguimiento se aplaza
    AUTO_REPLY    "fuera de la oficina"            ─┘
    OPT_OUT       pidió no ser contactado: OPT_OUT irreversible + todos sus contactos a no contactar
    REPLY         cualquier otra respuesta: REPLIED y se cancelan los pendientes del lead

Una baja nunca depende del LLM (ADR 0006): se detecta con reglas sobre el texto nuevo, sin lo citado.
Todo es idempotente: reprocesar un correo no repite el efecto.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from leadbot.core.clock import to_db, utcnow
from leadbot.core.config import load_yaml
from leadbot.core.db import session_scope
from leadbot.leads.models import Company, ContactChannel, Event, Message
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import Channel, LeadStatus, MessageStatus
from leadbot.outreach import control
from leadbot.outreach.channels.email_gmail import GmailChannel
from leadbot.outreach.channels.mailparse import MSGID_RE, IncomingMail

log = logging.getLogger(__name__)

FOLDER = "INBOX"
FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "live.com",
    "yahoo.com", "yahoo.es", "icloud.com", "msn.com", "proton.me", "protonmail.com",
}  # fmt: skip


class Kind(StrEnum):
    REPLY = "reply"
    OPT_OUT = "opt_out"
    BOUNCE = "bounce"
    SOFT_BOUNCE = "soft_bounce"
    AUTO_REPLY = "auto_reply"


# --- Clasificación (funciones puras) ------------------------------------------------------------


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


QUOTE_START_RE = re.compile(
    r"^(>|-{2,}\s*(mensaje original|original message|forwarded)|_{10,}|de:\s|from:\s|enviado desde|sent from)", re.I
)


def strip_quoted(text: str) -> str:
    """El texto nuevo de una respuesta, sin el mensaje citado ni las firmas de 'enviado desde'."""
    lines = text.replace("\r", "").split("\n")
    kept: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        attribution = re.match(r"^(el|on)\s", stripped, re.I) and re.search(
            r"escribi[oó]|wrote", " ".join(lines[i : i + 2]), re.I
        )
        if kept and (QUOTE_START_RE.match(stripped) or attribution):
            break
        kept.append(line)
    return "\n".join(kept).strip()


OPT_OUT_PHRASES = re.compile(
    r"\bno (?:me |nos )?interesa\b|\bno (?:estoy|estamos) interesad|"
    r"\bno (?:me |nos )?(?:escribas|escriban|escribir|contactes|contacten|contactar|molestes|molesten|"
    r"envies|envien|enviar|llames|llamen|llamar)\b|"
    r"\b(?:vuelvas|vuelvan|volver) a (?:escribir|contactar)|"
    r"\bdar(?:me|nos|se)? de baja|\b(?:quitar|eliminar|sacar)(?:me|nos)? de (?:su|la|sus|tu) (?:lista|base)|"
    r"\bunsubscribe\b|\bremove me\b|\bno (?:deseo|deseamos|quiero|queremos) recibir|\bes spam\b"
)
OPT_OUT_FIRST_LINES = {"no", "no gracias", "baja", "stop", "remover", "cancelar", "nel"}
AUTO_SUBJECT_RE = re.compile(
    r"^(re:\s*)*(respuesta autom|automatic reply|auto(?:matic)?[- ]?reply|autoreply|fuera de(?:l)? (?:la )?oficina|"
    r"out of office|ausente|vacation)",
    re.I,
)
BOUNCE_SENDER_RE = re.compile(r"^(mailer-daemon|postmaster|mail delivery)", re.I)
HARD_BOUNCE_RE = re.compile(
    r"does not exist|not found|unknown user|no such user|user unknown|mailbox unavailable|couldn.t be found|"
    r"no existe|address rejected|invalid recipient",
    re.I,
)


def is_opt_out(text: str, subject: str = "") -> bool:
    fresh = normalize(strip_quoted(text))
    if normalize(subject).strip() == "baja":  # el mailto de List-Unsubscribe
        return True
    first = next((ln for ln in fresh.splitlines() if ln.strip()), "")
    first = re.sub(r"[^\w\s]", "", first).strip()
    if first in OPT_OUT_FIRST_LINES or first.startswith("no gracias"):
        return True
    return bool(OPT_OUT_PHRASES.search(fresh))


def bounce_is_hard(raw: str) -> bool:
    status = re.search(r"Status:\s*([245])\.\d+\.\d+", raw)
    if status:
        return status.group(1) == "5"
    if re.search(r"Action:\s*delayed", raw, re.I):
        return False
    return bool(HARD_BOUNCE_RE.search(raw))


def classify(mail: IncomingMail) -> Kind:
    local = mail.sender.partition("@")[0]
    if mail.is_dsn or BOUNCE_SENDER_RE.match(local):
        return Kind.BOUNCE if bounce_is_hard(mail.raw) else Kind.SOFT_BOUNCE
    if mail.auto_submitted or AUTO_SUBJECT_RE.match(mail.subject.strip()):
        return Kind.AUTO_REPLY
    if is_opt_out(mail.text, mail.subject):
        return Kind.OPT_OUT
    return Kind.REPLY


# --- Emparejamiento -----------------------------------------------------------------------------


@dataclass
class Match:
    company: Company
    contact: ContactChannel
    message: Message | None  # el mensaje enviado al que responde (el más reciente si se empareja por dirección)


def _latest_sent(session: Session, contact_id: int) -> Message | None:
    return session.scalar(
        select(Message)
        .where(Message.contact_id == contact_id, Message.status == MessageStatus.SENT)
        .order_by(Message.step.desc(), Message.id.desc())
        .limit(1)
    )


def _by_message_ids(session: Session, ids: list[str]) -> Match | None:
    if not ids:
        return None
    message = session.scalar(
        select(Message).where(Message.external_id.in_(ids)).order_by(Message.step.desc(), Message.id.desc()).limit(1)
    )
    if message is None:
        return None
    return Match(session.get(Company, message.company_id), message.contact, message)


def _by_address(session: Session, address: str) -> Match | None:
    if not address:
        return None
    contacts = list(
        session.scalars(
            select(ContactChannel).where(
                ContactChannel.channel == Channel.EMAIL, func.lower(ContactChannel.value) == address.lower()
            )
        )
    )
    candidates = [(c, _latest_sent(session, c.id)) for c in contacts]
    candidates = [(c, m) for c, m in candidates if m is not None]
    if not candidates:
        return None
    contact, message = max(candidates, key=lambda cm: cm[1].sent_at or datetime.min)
    return Match(session.get(Company, contact.company_id), contact, message)


def _by_domain(session: Session, address: str) -> Match | None:
    """Otra dirección del mismo dominio de empresa (ventas@ responde por info@). No aplica a correos gratuitos."""
    domain = address.rpartition("@")[2].lower()
    if not domain or domain in FREE_MAIL:
        return None
    contacts = list(
        session.scalars(
            select(ContactChannel).where(
                ContactChannel.channel == Channel.EMAIL, func.lower(ContactChannel.value).like(f"%@{domain}")
            )
        )
    )
    candidates = [(c, _latest_sent(session, c.id)) for c in contacts]
    candidates = [(c, m) for c, m in candidates if m is not None]
    if len({c.company_id for c, _ in candidates}) != 1:  # ninguna o ambigua: mejor no adivinar
        return None
    contact, message = candidates[0]
    return Match(session.get(Company, contact.company_id), contact, message)


def find_match(session: Session, mail: IncomingMail, kind: Kind) -> Match | None:
    """Encabezados primero (In-Reply-To y References); luego la dirección; luego el dominio de la empresa."""
    if kind in (Kind.BOUNCE, Kind.SOFT_BOUNCE):
        # El informe de rebote trae el mensaje original: sus Message-ID y la dirección que falló.
        match = _by_message_ids(session, list(dict.fromkeys(MSGID_RE.findall(mail.raw))))
        if match:
            return match
        failed = re.search(
            r"(?:Final-Recipient|X-Failed-Recipients|Original-Recipient):\s*(?:rfc822;\s*)?<?([^\s<>;]+@[^\s<>;]+)",
            mail.raw,
            re.I,
        )
        return _by_address(session, failed.group(1)) if failed else None
    ids = [i for i in (mail.in_reply_to, *mail.references) if i]
    return _by_message_ids(session, ids) or _by_address(session, mail.sender) or _by_domain(session, mail.sender)


# --- Efectos ------------------------------------------------------------------------------------


def _snippet(mail: IncomingMail) -> str:
    return re.sub(r"\s+", " ", strip_quoted(mail.text))[:300]


def _lead_has_other_path(session: Session, company_id: int, contact_id: int) -> bool:
    """¿Al lead le queda otro camino (otro canal enviado o por enviar) además del contacto que rebotó?"""
    return (
        session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.company_id == company_id,
                Message.contact_id != contact_id,
                Message.status.in_(
                    [MessageStatus.SENT, MessageStatus.APPROVED, MessageStatus.QUEUED, MessageStatus.PENDING_APPROVAL]
                ),
            )
        )
        > 0
    )


def apply_mail(session: Session, mail: IncomingMail, kind: Kind, match: Match, now: datetime) -> str:
    """Aplica el efecto de un correo ya emparejado. Devuelve lo que pasó (para las estadísticas)."""
    repo = LeadRepository(session)
    company, contact, message = match.company, match.contact, match.message
    message_id = message.id if message else None

    if kind == Kind.REPLY:
        if message is not None and message.reply_at is not None:
            return "duplicate"
        if message is not None:
            message.reply_at = to_db(now)
        if company.status == LeadStatus.CONTACTED:
            repo.change_status(company, LeadStatus.REPLIED, "respuesta por email")
        repo.cancel_open_messages(company.id, "el lead respondió")
        session.add(
            Event(
                company_id=company.id,
                message_id=message_id,
                kind="replied",
                data={"from": mail.sender, "text": _snippet(mail)},
            )
        )
        return "reply"

    if kind == Kind.OPT_OUT:
        if company.status == LeadStatus.OPT_OUT:
            return "duplicate"
        for value in {mail.sender, *(c.value for c in company.contacts)}:
            repo.add_suppression(value, "pidió no ser contactado (respuesta por email)")
        if message is not None and message.reply_at is None:
            message.reply_at = to_db(now)
        repo.change_status(company, LeadStatus.OPT_OUT, "baja por email")
        repo.cancel_open_messages(company.id, "el lead pidió la baja")
        session.add(
            Event(
                company_id=company.id,
                message_id=message_id,
                kind="opted_out",
                data={"from": mail.sender, "text": _snippet(mail)},
            )
        )
        return "opt_out"

    if kind == Kind.BOUNCE:
        if contact.invalid:
            return "duplicate"
        contact.invalid = True
        repo.add_suppression(contact.value, "rebote definitivo")
        if message is not None:
            message.error = "rebotó: la dirección no existe o rechazó el correo"
        repo.cancel_open_messages(company.id, "el email rebotó", contact_id=contact.id)
        if company.status == LeadStatus.CONTACTED and not _lead_has_other_path(session, company.id, contact.id):
            repo.change_status(company, LeadStatus.LOST, "rebote")
        session.add(Event(company_id=company.id, message_id=message_id, kind="bounced", data={"to": contact.value}))
        return "bounce"

    # SOFT_BOUNCE y AUTO_REPLY: constancia para aplazar el seguimiento; no cambian el estado.
    session.add(
        Event(
            company_id=company.id,
            message_id=message_id,
            kind="auto_reply",
            data={"kind": kind.value, "from": mail.sender},
        )
    )
    return "auto"


@dataclass
class ReplyStats:
    read: int = 0
    replies: int = 0
    opt_outs: int = 0
    bounces: int = 0
    auto: int = 0
    duplicates: int = 0
    unmatched: int = 0
    ignored: int = 0
    error: str = ""


def handle_mail(
    session: Session, mail: IncomingMail, now: datetime, own_address: str, stats: ReplyStats, *, dry_run: bool = False
) -> None:
    stats.read += 1
    if mail.sender == own_address.lower():
        stats.ignored += 1
        return
    kind = classify(mail)
    found = find_match(session, mail, kind)
    if found is None:
        # ¿Responde a algo nuestro? Si el asunto coincide con uno reciente, queda constancia para resolverlo a mano.
        subject = re.sub(r"^(re|rv):\s*", "", mail.subject.strip(), flags=re.I)
        if subject and subject in LeadRepository(session).recent_sent_subjects(now - timedelta(days=30)):
            if not dry_run:
                session.add(
                    Event(
                        kind="reply_unmatched",
                        data={"from": mail.sender, "subject": mail.subject, "text": _snippet(mail)},
                    )
                )
            stats.unmatched += 1
        else:
            stats.ignored += 1
        return
    log.info("correo de %s: %s", mail.sender, kind.value, extra={"company_id": found.company.id})
    if dry_run:
        outcome = {Kind.REPLY: "reply", Kind.OPT_OUT: "opt_out", Kind.BOUNCE: "bounce"}.get(kind, "auto")
    else:
        outcome = apply_mail(session, mail, kind, found, now)
    match outcome:
        case "reply":
            stats.replies += 1
        case "opt_out":
            stats.opt_outs += 1
        case "bounce":
            stats.bounces += 1
        case "auto":
            stats.auto += 1
        case _:
            stats.duplicates += 1


def bounce_guard(session: Session, now: datetime) -> bool:
    """Más rebotes de los tolerados en 24 h dañan la reputación de la cuenta: pausa el email. True si pausó."""
    limit = load_yaml("limits").get("reputation", {}).get("max_bounces_per_day", 3)
    bounces = LeadRepository(session).event_count_since("bounced", now - timedelta(hours=24))
    if bounces > limit:
        control.pause_channel(session, Channel.EMAIL, f"{bounces} rebotes en 24 horas (máximo {limit})")
        return True
    return False


def process_replies(adapter: GmailChannel, *, dry_run: bool = False, now: datetime | None = None) -> ReplyStats:
    """Lee el buzón desde el cursor y aplica cada correo en su propia transacción."""
    stats = ReplyStats()
    now = now or utcnow()
    with session_scope() as session:
        cursor = LeadRepository(session).get_cursor(FOLDER)
        after_uid, validity = (cursor.last_uid, cursor.uidvalidity) if cursor else (0, None)

    fetched = adapter.fetch_replies(after_uid=after_uid, uidvalidity=validity, folder=FOLDER)
    resumed = bool(after_uid) and validity == fetched.uidvalidity
    last_ok = after_uid if resumed else 0
    for mail in sorted(fetched.mails, key=lambda m: m.uid):
        try:
            with session_scope() as session:
                handle_mail(session, mail, now, adapter.address, stats, dry_run=dry_run)
        except Exception as e:  # el cursor no avanza más allá de un correo que no se pudo procesar
            log.exception("no se pudo procesar el correo %s", mail.uid)
            stats.error = f"correo {mail.uid}: {type(e).__name__}: {e}"
            break
        last_ok = mail.uid
    if not stats.error:
        last_ok = max(last_ok, fetched.last_uid)

    if not dry_run:
        with session_scope() as session:
            LeadRepository(session).save_cursor(FOLDER, fetched.uidvalidity, last_ok)
            if stats.bounces:
                bounce_guard(session, now)
    return stats
