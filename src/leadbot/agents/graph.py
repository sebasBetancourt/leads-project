"""Grafo del agente por lead (patrón graph.py de kaymen99, simplificado).

    review_website ─┐
                    ├─► qualify ──qualified──► draft_messages ─► save ─► END
search_web_presence ┘       └──not_qualified────────────────────► save

El bucle "siguiente lead" no vive en el grafo (como en kaymen99) sino en el worker:
así cada lead es una ejecución independiente que se puede reintentar sola.
"""

from langgraph.graph import END, StateGraph

from leadbot.agents.nodes import LeadAgentNodes
from leadbot.agents.state import AgentState
from leadbot.tools.base import Tool


def build_lead_graph(tools: dict[str, Tool]):
    nodes = LeadAgentNodes(tools)
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
