"""Grafo del agente por lead (patrón graph.py de kaymen99, simplificado).

    review_website ─┐
                    ├─► qualify ──qualified──► draft_messages ─► save ─► END
search_web_presence ┘       └──not_qualified────────────────────► save

El bucle "siguiente lead" no vive en el grafo (como en kaymen99) sino en el worker:
así cada lead es una ejecución independiente que se puede reintentar sola.

Volver a redactar (REHACER en Sheets) usa un grafo corto que reutiliza la investigación guardada:

    draft_messages ─► save ─► END

Los seguimientos (F3) usan otro grafo corto, también sobre la investigación guardada:

    draft_follow_up ─► save_follow_up ─► END
"""

from langgraph.graph import END, StateGraph

from leadbot.agents.nodes import LeadAgentNodes
from leadbot.agents.state import AgentState
from leadbot.tools.base import Tool


def build_lead_graph(tools: dict[str, Tool], *, dry_run: bool = False):
    nodes = LeadAgentNodes(tools, dry_run=dry_run)
    graph = StateGraph(AgentState)

    graph.add_node("review_website", nodes.review_website)
    graph.add_node("search_web_presence", nodes.search_web_presence)
    graph.add_node("qualify", nodes.qualify)
    graph.add_node("draft_messages", nodes.draft_messages)
    graph.add_node("save", nodes.save)

    # Las dos investigaciones corren en paralelo y convergen en qualify.
    graph.set_entry_point("review_website")
    graph.set_entry_point("search_web_presence")
    graph.add_edge(["review_website", "search_web_presence"], "qualify")

    graph.add_conditional_edges(
        "qualify",
        nodes.route_after_qualify,
        {"qualified": "draft_messages", "not_qualified": "save"},
    )
    graph.add_edge("draft_messages", "save")
    graph.add_edge("save", END)
    return graph.compile()


def build_redraft_graph(tools: dict[str, Tool], *, dry_run: bool = False):
    nodes = LeadAgentNodes(tools, dry_run=dry_run)
    graph = StateGraph(AgentState)
    graph.add_node("draft_messages", nodes.draft_messages)
    graph.add_node("save", nodes.save)
    graph.set_entry_point("draft_messages")
    graph.add_edge("draft_messages", "save")
    graph.add_edge("save", END)
    return graph.compile()


def build_follow_up_graph(*, dry_run: bool = False):
    nodes = LeadAgentNodes({}, dry_run=dry_run)
    graph = StateGraph(AgentState)
    graph.add_node("draft_follow_up", nodes.draft_follow_up)
    graph.add_node("save_follow_up", nodes.save_follow_up)
    graph.set_entry_point("draft_follow_up")
    graph.add_edge("draft_follow_up", "save_follow_up")
    graph.add_edge("save_follow_up", END)
    return graph.compile()
