"""Tool: búsqueda web (noticias, reseñas, presencia en marketplaces o domicilios)."""

from pydantic import BaseModel, Field

from leadbot.tools.base import Tool, ToolResult


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="Búsqueda, ej. '\"Ferretería El Tornillo\" Pereira'")
    max_results: int = Field(5, le=10)


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Busca en internet información pública del negocio: noticias, reseñas, si vende en "
        "Rappi, Mercado Libre o redes. Devuelve títulos, enlaces y fragmentos."
    )
    args_schema = WebSearchArgs

    def __init__(self, provider: str = "duckduckgo", api_key: str = ""):
        self.provider = provider  # duckduckgo | serpapi (WEB_SEARCH_PROVIDER)
        self.api_key = api_key

    def run(self, args: WebSearchArgs) -> ToolResult:
        raise NotImplementedError("F2")
