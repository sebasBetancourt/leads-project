"""Estado que viaja por el grafo del agente (patrón state.py de kaymen99), un lead a la vez."""

from operator import add
from typing import Annotated, TypedDict

from pydantic import BaseModel

from leadbot.agents.schemas import LeadQualification
from leadbot.leads.status import Channel


class LeadContext(TypedDict):
    company_id: int
    name: str
    sector: str
    city: str
    website: str | None
    google_category: str | None
    rating: float | None
    reviews_count: int | None
    channels: dict[str, list[str]]  # canal -> valores (de enrichment), sin los de la lista de no contactar


class MessageDraft(BaseModel):
    """Mensaje listo para guardar: el texto del LLM más el destinatario y las alertas de agents/checks.py."""

    channel: Channel
    recipient: str
    subject: str | None = None
    body: str
    flags: list[str] = []


class AgentState(TypedDict, total=False):
    lead: LeadContext
    research_notes: Annotated[list[str], add]  # cada nodo de investigación agrega aquí
    signals: dict  # enrichment.signals.SiteSignals de su web
    qualification: LeadQualification
    drafts: list[MessageDraft]
    usage: Annotated[list[dict], add]  # una entrada por llamada al LLM (tokens, costo, modelo)
    # Para volver a redactar (REHACER en Sheets): la investigación que se reutiliza y tus notas.
    research_id: int | None
    redraft_channels: list[Channel]
    feedback: list[str]
    proposal_path: str | None
    error: str | None
