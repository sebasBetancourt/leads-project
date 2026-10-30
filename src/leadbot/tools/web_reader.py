"""Tool: lee una página y la devuelve como texto limpio (patrón markdown_scraper_tool de kaymen99)."""

import re

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from leadbot.enrichment.crawler import fetch_page
from leadbot.tools.base import Tool, ToolResult

NOISE_TAGS = ("script", "style", "noscript", "svg", "iframe", "template", "link", "meta")
SPACES_RE = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def html_to_text(html: str) -> str:
    """Título, descripción y texto visible, sin scripts ni estilos, con los espacios compactados."""
    tree = HTMLParser(html)
    title = tree.css_first("title")
    description = tree.css_first('meta[name="description"]')
    head = [
        f"Título: {title.text(strip=True)}" if title and title.text(strip=True) else "",
        f"Descripción: {description.attributes.get('content', '').strip()}" if description else "",
    ]
    tree.strip_tags(list(NOISE_TAGS))
    body = tree.body.text(separator="\n") if tree.body else ""
    body = BLANK_LINES_RE.sub("\n", SPACES_RE.sub(" ", body)).strip()
    return "\n".join([line for line in head if line] + [body]).strip()


class WebReaderArgs(BaseModel):
    url: str = Field(..., description="URL a leer")
    max_chars: int = Field(6000, le=20000)


class WebReaderTool(Tool):
    name = "web_reader"
    description = (
        "Lee una página web y devuelve su texto. Úsala para revisar la web del negocio: "
        "si tiene tienda en línea, catálogo, pasarela de pagos, o si se ve desactualizada."
    )
    args_schema = WebReaderArgs

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def run(self, args: WebReaderArgs) -> ToolResult:
        """Reutiliza enrichment.crawler y convierte el HTML a texto con selectolax.

        `data` trae el HTML crudo para enrichment.signals: el LLM solo ve el texto recortado.
        """
        page = fetch_page(args.url, timeout=self.timeout)
        if page.error:
            return ToolResult(ok=False, content=f"No se pudo leer {page.url}: {page.error}", data={"url": page.url})
        text = html_to_text(page.html)
        if len(text) > args.max_chars:
            text = text[: args.max_chars].rsplit(" ", 1)[0] + " […]"
        return ToolResult(ok=True, content=text, data={"url": page.url, "html": page.html})
