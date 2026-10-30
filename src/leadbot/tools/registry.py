"""Registro central de tools: el agente recibe solo las que se le pasen desde aquí."""

from leadbot.core.config import get_settings
from leadbot.tools.base import Tool
from leadbot.tools.knowledge_base import KnowledgeBaseTool
from leadbot.tools.web_reader import WebReaderTool
from leadbot.tools.web_search import WebSearchTool


def default_tools() -> dict[str, Tool]:
    settings = get_settings()
    tools: list[Tool] = [
        KnowledgeBaseTool(),
        WebReaderTool(),
        WebSearchTool(settings.web_search_provider, settings.serpapi_api_key),
    ]
    return {t.name: t for t in tools}
