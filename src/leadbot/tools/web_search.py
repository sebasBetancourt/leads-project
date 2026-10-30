"""Tool: búsqueda web (noticias, reseñas, presencia en marketplaces o domicilios).

Proveedores (WEB_SEARCH_PROVIDER):
- duckduckgo: gratis y sin key (paquete ddgs). Puede bloquear si se le pregunta muy seguido.
- serpapi: resultados de Google, con key de pago.
Si la búsqueda falla, la tool devuelve ok=False y el agente sigue con lo que tenga.
"""

import threading
import time

import httpx
from pydantic import BaseModel, Field

from leadbot.tools.base import Tool, ToolResult

SNIPPET_CHARS = 300
MIN_SECONDS_BETWEEN_CALLS = 2.0  # cortesía con DuckDuckGo; evita el bloqueo por ráfagas


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str = ""


def format_hits(hits: list[SearchHit]) -> str:
    return "\n".join(
        f"{i}. {h.title} — {h.url}\n   {h.snippet[:SNIPPET_CHARS]}".rstrip() for i, h in enumerate(hits, 1)
    )


def search_duckduckgo(query: str, max_results: int) -> list[SearchHit]:
    from ddgs import DDGS

    results = DDGS().text(query, region="co-es", safesearch="moderate", max_results=max_results)
    return [SearchHit(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", "")) for r in results]


def search_serpapi(query: str, max_results: int, api_key: str) -> list[SearchHit]:
    if not api_key:
        raise RuntimeError("Falta SERPAPI_API_KEY")
    response = httpx.get(
        "https://serpapi.com/search.json",
        params={"engine": "google", "q": query, "gl": "co", "hl": "es", "num": max_results, "api_key": api_key},
        timeout=20,
    )
    response.raise_for_status()
    return [
        SearchHit(title=r.get("title", ""), url=r.get("link", ""), snippet=r.get("snippet", ""))
        for r in response.json().get("organic_results", [])[:max_results]
    ]


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

    _lock = threading.Lock()
    _last_call = 0.0

    def __init__(self, provider: str = "duckduckgo", api_key: str = ""):
        self.provider = provider  # duckduckgo | serpapi (WEB_SEARCH_PROVIDER)
        self.api_key = api_key

    def _throttle(self) -> None:
        with WebSearchTool._lock:
            wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - WebSearchTool._last_call)
            if wait > 0:
                time.sleep(wait)
            WebSearchTool._last_call = time.monotonic()

    def run(self, args: WebSearchArgs) -> ToolResult:
        self._throttle()
        if self.provider == "serpapi":
            hits = search_serpapi(args.query, args.max_results, self.api_key)
        elif self.provider == "duckduckgo":
            hits = search_duckduckgo(args.query, args.max_results)
        else:
            return ToolResult(ok=False, content=f"Proveedor de búsqueda desconocido: {self.provider}")
        if not hits:
            return ToolResult(ok=True, content="Sin resultados.", data={"hits": []})
        return ToolResult(ok=True, content=format_hits(hits), data={"hits": [h.model_dump() for h in hits]})
