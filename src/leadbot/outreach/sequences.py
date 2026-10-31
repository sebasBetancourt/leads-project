"""Seguimientos: si no hay respuesta a los N días (limits.yaml -> sequences), se redacta el siguiente paso.

Los días cuentan desde el primer email (paso 0): con `follow_up_days: [3, 7]` el paso 1 toca al día 3 y el paso 2
al día 7. Los seguimientos también pasan por aprobación (Message.step >= 1 en la pestaña Aprobación) y salen en el
mismo hilo. Una respuesta, una baja o un rebote cancelan lo pendiente (replies.py). Cuando se acaban los pasos y
nadie responde, el lead pasa a LOST ("sin_respuesta").

Este módulo solo decide *cuándo* toca y encola el trabajo `followup`; quien redacta es el agente (agents/followup.py),
porque `outreach` no conoce al agente (regla de dependencias de ARCHITECTURE.md).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from leadbot.core.clock import from_db, utcnow
from leadbot.core.config import load_yaml
from leadbot.leads.models import Company, ContactChannel, Event, Message
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import Channel, LeadStatus, MessageStatus
from leadbot.outreach import queue

log = logging.getLogger(__name__)

FOLLOW_UP_JOB = "followup"
AUTO_REPLY_PAUSE = timedelta(days=2)  # tras una respuesta automática se espera antes de insistir
LIVE = (MessageStatus.PENDING_APPROVAL, MessageStatus.APPROVED, MessageStatus.QUEUED)


@dataclass(frozen=True)
class DueFollowUp:
    company_id: int
    contact_id: int
    step: int
    redo_message_id: int | None = None  # REHACER en Sheets: el mensaje del que se parte con las notas del revisor

    @property
    def dedup_key(self) -> str:
        return f"{self.company_id}:{self.contact_id}:{self.step}"


def sequence_config() -> tuple[list[int], int]:
    cfg = load_yaml("limits")["sequences"]["email"]
    return list(cfg["follow_up_days"]), int(cfg.get("close_after_days", 7))


def _threads(session: Session) -> dict[tuple[int, int], list[Message]]:
    """Emails de los leads CONTACTED, por (negocio, destinatario), del paso 0 en adelante."""
    messages = session.scalars(
        select(Message)
        .join(Company)
        .where(Message.channel == Channel.EMAIL, Company.status == LeadStatus.CONTACTED)
        .order_by(Message.step, Message.id)
    )
    threads: dict[tuple[int, int], list[Message]] = {}
    for message in messages:
        threads.setdefault((message.company_id, message.contact_id), []).append(message)
    return threads


def _last_auto_reply(session: Session, company_id: int) -> datetime | None:
    moment = session.scalar(
        select(func.max(Event.created_at)).where(Event.company_id == company_id, Event.kind == "auto_reply")
    )
    return from_db(moment) if moment else None


def due_follow_ups(session: Session, now: datetime | None = None) -> list[DueFollowUp]:
    """Seguimientos que ya tocan (o rehacer uno que el revisor devolvió con notas)."""
    now = now or utcnow()
    days, _ = sequence_config()
    repo = LeadRepository(session)
    due = []
    for (company_id, contact_id), thread in _threads(session).items():
        first, last = thread[0], thread[-1]
        contact = session.get(ContactChannel, contact_id)
        if (
            first.step != 0
            or first.status != MessageStatus.SENT
            or contact.invalid
            or repo.is_suppressed(contact.value)
        ):
            continue
        if last.step >= 1 and last.status == MessageStatus.REJECTED and last.decision == "REHACER":
            due.append(DueFollowUp(company_id, contact_id, last.step, redo_message_id=last.id))
            continue
        if last.status != MessageStatus.SENT or last.step >= len(days):
            continue
        step = last.step + 1
        ready = from_db(first.sent_at) + timedelta(days=days[step - 1])
        ready = max(ready, from_db(last.sent_at) + timedelta(days=1))
        if auto := _last_auto_reply(session, company_id):
            ready = max(ready, auto + AUTO_REPLY_PAUSE)
        if now >= ready:
            due.append(DueFollowUp(company_id, contact_id, step))
    return due


def schedule_follow_ups(session: Session, now: datetime | None = None) -> int:
    """Encola un trabajo `followup` por cada seguimiento que toca. Devuelve cuántos nuevos."""
    count = 0
    for item in due_follow_ups(session, now):
        payload = {
            "company_id": item.company_id,
            "contact_id": item.contact_id,
            "step": item.step,
            "redo_message_id": item.redo_message_id,
        }
        count += queue.enqueue(session, FOLLOW_UP_JOB, payload, dedup_key=item.dedup_key) is not None
    return count


def close_finished_sequences(session: Session, now: datetime | None = None) -> int:
    """Leads CONTACTED a los que ya no se les va a escribir y que nunca respondieron pasan a LOST."""
    now = now or utcnow()
    days, close_after = sequence_config()
    repo = LeadRepository(session)
    by_company: dict[int, list[list[Message]]] = {}
    for (company_id, _), thread in _threads(session).items():
        by_company.setdefault(company_id, []).append(thread)

    closed = 0
    for company_id, threads in by_company.items():
        messages = list(session.scalars(select(Message).where(Message.company_id == company_id)))
        if any(m.status in LIVE for m in messages) or any(
            m.status == MessageStatus.SENT and m.channel != Channel.EMAIL for m in messages
        ):
            continue  # todavía hay algo en camino, o hay redes asistidas que esta secuencia no maneja
        last_sent = [from_db(m.sent_at) for m in messages if m.status == MessageStatus.SENT and m.sent_at]
        if not last_sent or now - max(last_sent) < timedelta(days=close_after):
            continue
        if not all(_finished(thread, len(days)) for thread in threads):
            continue
        company = session.get(Company, company_id, with_for_update=True)
        repo.change_status(company, LeadStatus.LOST, "sin_respuesta")
        closed += 1
    return closed


def _finished(thread: list[Message], steps: int) -> bool:
    """¿No se le va a escribir más por este hilo? Se mandó el último paso, o se descartó un seguimiento."""
    last = thread[-1]
    if last.status == MessageStatus.SENT:
        return last.step >= steps
    return last.step >= 1 and last.status == MessageStatus.REJECTED and last.decision == "DESCARTAR"


def cancel_pending_for_company(session: Session, company_id: int, reason: str) -> int:
    """Cancela lo que no ha salido (por aprobar, aprobado o en cola). Lo usan la respuesta y la baja."""
    return LeadRepository(session).cancel_open_messages(company_id, reason)
