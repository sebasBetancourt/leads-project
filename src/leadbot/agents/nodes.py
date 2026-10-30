"""Nodos del grafo. Cada uno recibe el estado y devuelve solo las claves que cambia."""

from leadbot.agents.state import AgentState
from leadbot.tools.base import Tool


class LeadAgentNodes:
    def __init__(self, tools: dict[str, Tool]):
        self.tools = tools

    def review_website(self, state: AgentState) -> AgentState:
        """Usa web_reader sobre la web del lead: ¿tienda en línea?, ¿pagos?, ¿desactualizada?"""
        raise NotImplementedError("F2")

    def search_web_presence(self, state: AgentState) -> AgentState:
        """Usa web_search: reseñas, marketplaces, domicilios, noticias."""
        raise NotImplementedError("F2")

    def qualify(self, state: AgentState) -> AgentState:
        """LLM con salida LeadQualification, con prompts/qualify.md + sectors.yaml + knowledge_base."""
        raise NotImplementedError("F2")

    def draft_messages(self, state: AgentState) -> AgentState:
        """Un DraftedMessage por cada canal de best_channels (máx. max_channels_per_lead)."""
        raise NotImplementedError("F2")

    def save(self, state: AgentState) -> AgentState:
        """Guarda Research y Messages (PENDING_APPROVAL) y mueve el lead de estado."""
        raise NotImplementedError("F2")

    @staticmethod
    def route_after_qualify(state: AgentState) -> str:
        return "qualified" if state["qualification"].qualified else "not_qualified"
