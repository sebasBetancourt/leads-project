"""Acceso a leads. Patrón LeadLoaderBase de kaymen99: el resto del sistema no sabe de SQL."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from leadbot.leads.models import Company, ContactChannel, Event, Suppression
from leadbot.leads.status import Channel, LeadStatus, ensure_transition


class LeadRepository:
    def __init__(self, session: Session):
        self.session = session

    def fetch_by_status(self, status: LeadStatus, limit: int = 50) -> list[Company]:
        stmt = select(Company).where(Company.status == status).order_by(Company.id).limit(limit)
        return list(self.session.scalars(stmt))

    def all_with_contacts(self) -> list[Company]:
        stmt = select(Company).options(selectinload(Company.contacts)).order_by(Company.id)
        return list(self.session.scalars(stmt))

    def count_by_status(self) -> dict[LeadStatus, int]:
        rows = self.session.execute(select(Company.status, func.count()).group_by(Company.status))
        return {status: count for status, count in rows}

    def upsert_company(
        self, *, place_id: str | None, phone_e164: str | None, fields: dict, sector: str, city: str, query: str
    ) -> tuple[Company, bool]:
        """Inserta o actualiza por place_id; si no hay, deduplica por teléfono. Devuelve (lead, es_nuevo)."""
        company = None
        if place_id:
            company = self.session.scalar(select(Company).where(Company.place_id == place_id))
        if company is None and phone_e164:
            company = self.session.scalar(select(Company).where(Company.phone_e164 == phone_e164))

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
