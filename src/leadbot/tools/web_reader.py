"""Tool: lee una página y la devuelve como texto limpio (patrón markdown_scraper_tool de kaymen99)."""

from pydantic import BaseModel, Field

from leadbot.tools.base import Tool, ToolResult


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

    def run(self, args: WebReaderArgs) -> ToolResult:
        """Reutiliza enrichment.crawler y convierte el HTML a texto con selectolax."""
        raise NotImplementedError("F2")
