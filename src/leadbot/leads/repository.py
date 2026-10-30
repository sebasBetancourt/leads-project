"""Acceso a leads. Patrón LeadLoaderBase de kaymen99: el resto del sistema no sabe de SQL."""

from sqlalchemy.orm import Session

from leadbot.leads.models import Company
from leadbot.leads.status import LeadStatus


class LeadRepository:
    def __init__(self, session: Session):
        self.session = session

    def fetch_by_status(self, status: LeadStatus, limit: int = 50) -> list[Company]:
        raise NotImplementedError("F1")

    def upsert_from_gmaps(self, row: dict, *, sector: str, city: str, query: str) -> Company:
        """Inserta o actualiza por place_id; si no hay, deduplica por teléfono."""
        raise NotImplementedError("F1")

    def change_status(self, company: Company, target: LeadStatus, reason: str = "") -> None:
        """Valida con ensure_transition y registra un Event."""
        raise NotImplementedError("F1")

    def is_suppressed(self, value: str) -> bool:
        raise NotImplementedError("F1")
