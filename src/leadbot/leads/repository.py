"""Acceso a leads. Patrón LeadLoaderBase de kaymen99: el resto del sistema no sabe de SQL."""

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from leadbot.leads.models import Company, ContactChannel, Event, Message, Research, Suppression
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
