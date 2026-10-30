"""Decisiones de aprobación tomadas en Sheets y su efecto en el mensaje y en el lead (ADR 0006).

Por mensaje:
    APROBADO   -> APPROVED (con tus ediciones de asunto y mensaje)
    REHACER    -> REJECTED; el agente lo vuelve a redactar con tus notas (las notas son obligatorias)
    DESCARTAR  -> REJECTED

Por lead, cuando ya no le quedan mensajes en PENDING_APPROVAL:
    algún APROBADO              -> APPROVED  (los REHACER de ese lead no se vuelven a redactar)
    si no, algún REHACER        -> DRAFTED   (el worker lo vuelve a redactar)
    todos DESCARTAR             -> UNQUALIFIED
"""

import difflib
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadbot.leads.models import Company, Event, Message
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import LeadStatus, MessageStatus


class Decision(StrEnum):
    APROBADO = "APROBADO"
    REHACER = "REHACER"
    DESCARTAR = "DESCARTAR"


@dataclass
class ReviewRow:
    """Lo editable de una fila de la pestaña Aprobación."""

    message_id: int
    subject: str
    body: str
    decision: str
    notes: str


@dataclass
class ReviewStats:
    edited: int = 0
    approved: int = 0
    redo: int = 0
    discarded: int = 0
    invalid: dict[int, str] = field(default_factory=dict)  # message_id -> por qué no se aplicó
    leads: dict[int, LeadStatus] = field(default_factory=dict)  # company_id -> estado nuevo


def parse_decision(raw: str) -> Decision | None:
    """'aprobado ', 'Rehacer' -> Decision. Vacío o desconocido -> None."""
    text = unicodedata.normalize("NFKD", (raw or "").strip().upper())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return Decision.__members__.get(text)


def decision_error(row: ReviewRow) -> str | None:
    """Por qué una decisión escrita no se puede aplicar (None = válida o vacía)."""
    if not row.decision.strip():
        return None
    decision = parse_decision(row.decision)
    if decision is None:
        return f"decisión desconocida '{row.decision.strip()}': usa APROBADO, REHACER o DESCARTAR"
    if decision == Decision.REHACER and not row.notes.strip():
        return "REHACER necesita notas: explica qué cambiar"
    if decision == Decision.APROBADO and not row.body.strip():
        return "el mensaje está vacío"
    return None


def lead_outcome(decisions: list[Decision]) -> LeadStatus:
    if Decision.APROBADO in decisions:
        return LeadStatus.APPROVED
    if Decision.REHACER in decisions:
        return LeadStatus.DRAFTED
    return LeadStatus.UNQUALIFIED


def _diff(before: str, after: str) -> str:
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))[:4000]


def apply_review(session: Session, rows: list[ReviewRow]) -> ReviewStats:
    """Aplica ediciones y decisiones. Idempotente: un mensaje que ya no está PENDING_APPROVAL se ignora."""
    stats = ReviewStats()
    repo = LeadRepository(session)
    touched: set[int] = set()

    for row in rows:
        message = session.get(Message, row.message_id)
        if message is None or message.status != MessageStatus.PENDING_APPROVAL:
            continue

        subject = row.subject.strip() or None
        body = row.body.strip()
        if body and (body != message.body or subject != message.subject):
            session.add(
                Event(
                    company_id=message.company_id,
                    message_id=message.id,
                    kind="message_edited",
                    data={"subject": [message.subject, subject], "diff": _diff(message.body, body)},
                )
            )
            message.subject, message.body = subject, body
            stats.edited += 1

        if error := decision_error(row):
            stats.invalid[message.id] = error
            continue
        decision = parse_decision(row.decision)
        if decision is None:
            continue

        message.decision = decision.value
        message.reviewer_notes = row.notes.strip() or None
        message.status = MessageStatus.APPROVED if decision == Decision.APROBADO else MessageStatus.REJECTED
        session.add(
            Event(
                company_id=message.company_id,
                message_id=message.id,
                kind="message_reviewed",
                data={"decision": decision.value, "notes": message.reviewer_notes},
            )
        )
        stats.approved += decision == Decision.APROBADO
        stats.redo += decision == Decision.REHACER
        stats.discarded += decision == Decision.DESCARTAR
        touched.add(message.company_id)

    session.flush()
    for company_id in sorted(touched):
        company = session.get(Company, company_id, with_for_update=True, populate_existing=True)
        if company.status != LeadStatus.PENDING_APPROVAL:
            continue
        round_messages = list(
            session.scalars(
                select(Message).where(
                    Message.company_id == company_id,
                    Message.status.in_(
                        [MessageStatus.PENDING_APPROVAL, MessageStatus.APPROVED, MessageStatus.REJECTED]
                    ),
                )
            )
        )
        if any(m.status == MessageStatus.PENDING_APPROVAL for m in round_messages):
            continue  # faltan decisiones: el lead espera
        # Solo cuentan los mensajes de esta ronda: los REHACER viejos ya tienen uno más nuevo.
        current = {m.id for m in repo.messages_to_redraft(company_id)}
        decisions = [
            Decision(m.decision)
            for m in round_messages
            if m.decision and (m.decision != Decision.REHACER or m.id in current)
        ]
        target = lead_outcome(decisions)
        repo.change_status(company, target, "revisión en Sheets")
        stats.leads[company_id] = target
    return stats
