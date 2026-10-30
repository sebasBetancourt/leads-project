"""Estado que viaja por el grafo del agente (patrón state.py de kaymen99), un lead a la vez."""

from operator import add
from typing import Annotated, TypedDict

from leadbot.agents.schemas import DraftedMessage, LeadQualification


class LeadContext(TypedDict):
    company_id: int
    name: str
    sector: str
    city: str
    website: str | None
    google_category: str | None
    rating: float | None
    reviews_count: int | None
    channels: dict[str, list[str]]  # canal -> valores (de enrichment)


class AgentState(TypedDict, total=False):
    lead: LeadContext
    research_notes: Annotated[list[str], add]  # cada nodo de investigación agrega aquí
    qualification: LeadQualification
    messages: list[DraftedMessage]
    proposal_path: str | None
    error: str | None
