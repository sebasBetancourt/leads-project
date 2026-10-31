"""Acceso a leads. Patrón LeadLoaderBase de kaymen99: el resto del sistema no sabe de SQL."""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from leadbot.core.clock import from_db, to_db
from leadbot.leads.models import Company, ContactChannel, Event, MailCursor, Message, Research, Suppression
from leadbot.leads.status import Channel, LeadStatus, MessageStatus, ensure_transition

if TYPE_CHECKING:  # solo para tipos: leads no depende de agents en tiempo de ejecución
    from leadbot.agents.schemas import LeadQualification
    from leadbot.agents.state import MessageDraft


class LeadStateChanged(RuntimeError):
    """El lead cambió de estado mientras el agente trabajaba (otro proceso lo tomó): no se guarda nada."""


class LeadRepository:
    def __init__(self, session: Session):
        self.session = session

    def fetch_by_status(self, status: LeadStatus, limit: int = 50) -> list[Company]:
        stmt = select(Company).where(Company.status == status).order_by(Company.id).limit(limit)
        return list(self.session.scalars(stmt))

    def all_with_contacts(self) -> list[Company]:
        stmt = (
            select(Company).options(selectinload(Company.contacts), selectinload(Company.research)).order_by(Company.id)
        )
        return list(self.session.scalars(stmt))

    def latest_research(self, company_id: int) -> Research | None:
        return self.session.scalar(
            select(Research).where(Research.company_id == company_id).order_by(Research.id.desc()).limit(1)
        )

    def count_by_status(self) -> dict[LeadStatus, int]:
        rows = self.session.execute(select(Company.status, func.count()).group_by(Company.status))
        return {status: count for status, count in rows}

    def upsert_company(
        self, *, place_id: str | None, phone_e164: str | None, fields: dict, sector: str, city: str, query: str
    ) -> tuple[Company, bool]:
        """Inserta o actualiza por place_id; si no hay, deduplica por teléfono. Devuelve (lead, es_nuevo)."""
        company = self.find_existing(place_id, phone_e164)
        created = company is None
        if created:
            # Un negocio que aparece en dos búsquedas conserva la primera: sector, ciudad y source_query.
            company = Company(sector=sector, city=city, source_query=query, status=LeadStatus.NEW)
            self.session.add(company)

        company.place_id = place_id or company.place_id
        company.phone_e164 = phone_e164 or company.phone_e164
        for key, value in fields.items():
            setattr(company, key, value)
        self.session.flush()

        if created:
            self.session.add(Event(company_id=company.id, kind="discovered", data={"query": query}))
        return company, created

    def find_existing(self, place_id: str | None, phone_e164: str | None) -> Company | None:
        """El mismo negocio: por place_id de Google Maps o, si no, por teléfono (docs/DATA_MODEL.md)."""
        company = None
        if place_id:
            company = self.session.scalar(select(Company).where(Company.place_id == place_id))
        if company is None and phone_e164:
            company = self.session.scalar(select(Company).where(Company.phone_e164 == phone_e164))
        return company

    def add_contact(self, company: Company, channel: Channel, value: str, source: str) -> bool:
        """Agrega un canal si no existía. Devuelve True si es nuevo."""
        exists = self.session.scalar(
            select(ContactChannel.id).where(
                ContactChannel.company_id == company.id,
                ContactChannel.channel == channel,
                ContactChannel.value == value,
            )
        )
        if exists:
            return False
        self.session.add(ContactChannel(company_id=company.id, channel=channel, value=value, source=source))
        return True

    def change_status(self, company: Company, target: LeadStatus, reason: str = "") -> None:
        """Valida con ensure_transition y registra un Event."""
        previous = company.status
        company.status = ensure_transition(previous, target)
        self.session.add(
            Event(
                company_id=company.id,
                kind="status_changed",
                data={"from": previous.value, "to": target.value, "reason": reason},
            )
        )

    def is_suppressed(self, value: str) -> bool:
        return self.session.scalar(select(Suppression.id).where(Suppression.value == value)) is not None

    def suppressed_values(self, values: list[str]) -> set[str]:
        if not values:
            return set()
        return set(self.session.scalars(select(Suppression.value).where(Suppression.value.in_(values))))

    def contactable_channels(self, company: Company) -> dict[str, list[str]]:
        """Canal -> valores, sin los de la lista de no contactar. Lo que el agente puede usar."""
        blocked = self.suppressed_values([c.value for c in company.contacts])
        channels: dict[str, list[str]] = {}
        for contact in sorted(company.contacts, key=lambda c: (c.source != "website", c.id)):
            if contact.value not in blocked:
                channels.setdefault(contact.channel.value, []).append(contact.value)
        return channels

    def find_contact(self, company_id: int, channel: Channel, value: str) -> ContactChannel | None:
        return self.session.scalar(
            select(ContactChannel).where(
                ContactChannel.company_id == company_id,
                ContactChannel.channel == channel,
                ContactChannel.value == value,
            )
        )

    # --- Agente (F2) ---------------------------------------------------------------------

    def save_agent_result(
        self,
        *,
        company_id: int,
        qualification: "LeadQualification",
        drafts: list["MessageDraft"],
        research_id: int | None,
        research_data: dict[str, Any],
        model: str,
        tokens: int,
        cost_usd: float,
    ) -> list[Message]:
        """Una transacción: Research, mensajes en PENDING_APPROVAL y transiciones del lead.

        Primera vez (research_id None): ENRICHED -> QUALIFIED -> DRAFTED -> PENDING_APPROVAL, o -> UNQUALIFIED.
        Volver a redactar (REHACER en Sheets): DRAFTED -> PENDING_APPROVAL, reutilizando la investigación.
        """
        company = self.session.get(Company, company_id, with_for_update=True, populate_existing=True)
        redraft = research_id is not None
        expected = LeadStatus.DRAFTED if redraft else LeadStatus.ENRICHED
        if company is None or company.status != expected:
            raise LeadStateChanged(f"lead {company_id}: se esperaba {expected}, está en {company and company.status}")
        q = qualification
        if q.qualified and not drafts:
            raise ValueError(f"lead {company_id}: calificó pero no hay mensajes redactados")

        if redraft:
            research = self.session.get(Research, research_id)
            research.tokens += tokens
            research.cost_usd += cost_usd
            self.session.add(
                Event(company_id=company_id, kind="redrafted", data={"tokens": tokens, "cost_usd": cost_usd})
            )
        else:
            research = Research(
                company_id=company_id,
                summary=q.summary,
                needs=q.needs,
                score=q.score,
                qualified=q.qualified,
                case_study=q.case_study,
                model=model,
                data=research_data,
                tokens=tokens,
                cost_usd=cost_usd,
            )
            self.session.add(research)
            company.score = q.score
            if not q.qualified:
                self.change_status(company, LeadStatus.UNQUALIFIED, f"agente: {q.reason}"[:500])
                return []
            self.change_status(company, LeadStatus.QUALIFIED, f"agente: score {q.score}")
            self.change_status(company, LeadStatus.DRAFTED)

        messages = []
        for draft in drafts:
            contact = self.find_contact(company_id, draft.channel, draft.recipient)
            if contact is None or self.is_suppressed(draft.recipient):
                continue
            message = Message(
                company_id=company_id,
                contact_id=contact.id,
                channel=draft.channel,
                step=0,
                subject=draft.subject,
                body=draft.body,
                original_body=draft.body,
                review_flags=draft.flags,
                status=MessageStatus.PENDING_APPROVAL,
            )
            self.session.add(message)
            messages.append(message)
        if not messages:
            raise ValueError(f"lead {company_id}: ningún mensaje tiene un contacto válido")
        self.session.flush()
        for message in messages:
            self.session.add(
                Event(
                    company_id=company_id,
                    message_id=message.id,
                    kind="message_drafted",
                    data={"flags": message.review_flags},
                )
            )
        self.change_status(company, LeadStatus.PENDING_APPROVAL)
        return messages

    def save_follow_up(
        self,
        *,
        company_id: int,
        contact_id: int,
        step: int,
        draft: "MessageDraft",
        research_id: int,
        tokens: int,
        cost_usd: float,
        redo_message_id: int | None = None,
    ) -> Message:
        """Seguimiento nuevo (o rehecho tras un REHACER) en PENDING_APPROVAL. El lead sigue en CONTACTED."""
        company = self.session.get(Company, company_id, with_for_update=True, populate_existing=True)
        if company is None or company.status != LeadStatus.CONTACTED:
            raise LeadStateChanged(f"lead {company_id}: se esperaba CONTACTED, está en {company and company.status}")
        contact = self.session.get(ContactChannel, contact_id)
        if contact is None or contact.invalid or self.is_suppressed(contact.value):
            raise LeadStateChanged(f"lead {company_id}: el contacto {contact_id} ya no se puede usar")
        in_thread = (Message.company_id == company_id, Message.contact_id == contact_id)
        if redo_message_id is None:
            newest_step = self.session.scalar(select(func.max(Message.step)).where(*in_thread))
            if newest_step is not None and newest_step >= step:
                raise LeadStateChanged(f"lead {company_id}: el seguimiento {step} ya existe")
        else:
            newest_id = self.session.scalar(select(func.max(Message.id)).where(*in_thread, Message.step == step))
            if newest_id != redo_message_id:
                raise LeadStateChanged(f"lead {company_id}: el seguimiento {step} ya se rehízo")

        message = Message(
            company_id=company_id,
            contact_id=contact_id,
            channel=draft.channel,
            step=step,
            subject=draft.subject,
            body=draft.body,
            original_body=draft.body,
            review_flags=draft.flags,
            status=MessageStatus.PENDING_APPROVAL,
        )
        self.session.add(message)
        research = self.session.get(Research, research_id)
        research.tokens += tokens
        research.cost_usd += cost_usd
        self.session.flush()
        self.session.add(
            Event(
                company_id=company_id,
                message_id=message.id,
                kind="message_drafted",
                data={"flags": message.review_flags, "step": step, "tokens": tokens, "cost_usd": cost_usd},
            )
        )
        return message

    def pending_approval_messages(self) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.status == MessageStatus.PENDING_APPROVAL)
            .options(selectinload(Message.company).selectinload(Company.research), selectinload(Message.contact))
            .order_by(Message.id)
        )
        return list(self.session.scalars(stmt))

    def messages_to_redraft(self, company_id: int) -> list[Message]:
        """Mensajes marcados REHACER que todavía no tienen uno más nuevo en el mismo canal."""
        rejected = self.session.scalars(
            select(Message).where(
                Message.company_id == company_id,
                Message.status == MessageStatus.REJECTED,
                Message.decision == "REHACER",
            )
        )
        newest = dict(
            self.session.execute(
                select(Message.channel, func.max(Message.id))
                .where(Message.company_id == company_id)
                .group_by(Message.channel)
            ).all()
        )
        return [m for m in rejected if newest.get(m.channel) == m.id]

    # --- Envío (F3) ----------------------------------------------------------------------

    def _sent_dates(self, channel: Channel, tz: ZoneInfo) -> list[date]:
        """Día local de cada mensaje enviado por el canal (el volumen es de cientos: se agrupa en Python)."""
        sent = self.session.scalars(
            select(Message.sent_at).where(Message.channel == channel, Message.status == MessageStatus.SENT)
        )
        return [from_db(moment).astimezone(tz).date() for moment in sent if moment is not None]

    def sent_today(self, channel: Channel, now: datetime, tz: ZoneInfo) -> int:
        today = now.astimezone(tz).date()
        return sum(1 for day in self._sent_dates(channel, tz) if day == today)

    def days_active(self, channel: Channel, now: datetime, tz: ZoneInfo) -> int:
        """Días anteriores a hoy en que el canal envió algo (alimenta el calentamiento de la cuenta)."""
        today = now.astimezone(tz).date()
        return len({day for day in self._sent_dates(channel, tz) if day < today})

    def last_sent(self, channel: Channel) -> tuple[int, datetime] | None:
        """(id del mensaje, cuándo salió) del último envío del canal."""
        row = self.session.execute(
            select(Message.id, Message.sent_at)
            .where(Message.channel == channel, Message.status == MessageStatus.SENT, Message.sent_at.is_not(None))
            .order_by(Message.sent_at.desc(), Message.id.desc())
            .limit(1)
        ).first()
        return None if row is None else (row[0], from_db(row[1]))

    def add_suppression(self, value: str, reason: str) -> bool:
        """Agrega a la lista de no contactar. True si es nuevo."""
        value = value.strip()
        if not value or self.is_suppressed(value):
            return False
        self.session.add(Suppression(value=value, reason=reason[:255]))
        return True

    def cancel_open_messages(self, company_id: int, reason: str, *, contact_id: int | None = None) -> int:
        """Cancela lo que no ha salido (PENDING_APPROVAL, APPROVED, QUEUED) y los trabajos de envío pendientes."""
        stmt = select(Message).where(
            Message.company_id == company_id,
            Message.status.in_([MessageStatus.PENDING_APPROVAL, MessageStatus.APPROVED, MessageStatus.QUEUED]),
        )
        if contact_id is not None:
            stmt = stmt.where(Message.contact_id == contact_id)
        messages = list(self.session.scalars(stmt))
        for message in messages:
            message.status = MessageStatus.CANCELLED
            self.session.add(
                Event(company_id=company_id, message_id=message.id, kind="message_cancelled", data={"reason": reason})
            )
        if messages:
            from leadbot.leads.models import Job

            ids = [str(m.id) for m in messages]
            for job in self.session.scalars(
                select(Job).where(Job.kind == "send", Job.dedup_key.in_(ids), Job.status == "pending")
            ):
                job.status = "failed"
                job.last_error = f"cancelado: {reason}"
        return len(messages)

    def thread_ids(self, message: Message) -> list[str]:
        """Message-ID de los mensajes anteriores del mismo hilo (negocio y destinatario), del más viejo al más nuevo."""
        return list(
            self.session.scalars(
                select(Message.external_id)
                .where(
                    Message.company_id == message.company_id,
                    Message.contact_id == message.contact_id,
                    Message.step < message.step,
                    Message.external_id.is_not(None),
                    Message.status == MessageStatus.SENT,
                )
                .order_by(Message.step)
            )
        )

    def get_cursor(self, folder: str) -> MailCursor | None:
        return self.session.get(MailCursor, folder)

    def save_cursor(self, folder: str, uidvalidity: int | None, last_uid: int) -> None:
        cursor = self.session.get(MailCursor, folder)
        if cursor is None:
            self.session.add(MailCursor(folder=folder, uidvalidity=uidvalidity, last_uid=last_uid))
        else:
            cursor.uidvalidity, cursor.last_uid = uidvalidity, last_uid

    def event_count_since(self, kind: str, since: datetime) -> int:
        return self.session.scalar(
            select(func.count()).select_from(Event).where(Event.kind == kind, Event.created_at >= to_db(since))
        )

    def recent_sent_subjects(self, since: datetime) -> set[str]:
        rows = self.session.scalars(
            select(Message.subject).where(
                Message.status == MessageStatus.SENT, Message.sent_at >= to_db(since), Message.subject.is_not(None)
            )
        )
        return set(rows)
