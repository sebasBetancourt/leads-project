"""Contrato de las herramientas que el agente puede llamar por su cuenta (patrón src/tools de kaymen99).

Diferencia con enrichment/: enrichment es un paso fijo del pipeline que corre siempre igual;
una tool la decide usar el LLM durante la investigación ("busca noticias de este negocio").
Las tools reutilizan el código de enrichment/ y discovery/, no lo duplican.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel


class ToolResult(BaseModel):
    ok: bool
    content: str  # texto que se le devuelve al LLM (corto: es contexto pagado)
    data: dict[str, Any] = {}


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]  # el LLM decide cuándo usarla leyendo esto
    args_schema: ClassVar[type[BaseModel]]

    @abstractmethod
    def run(self, args: BaseModel) -> ToolResult: ...

    def __call__(self, **kwargs: Any) -> ToolResult:
        try:
            return self.run(self.args_schema(**kwargs))
        except Exception as e:  # una tool fallida no tumba al agente
            return ToolResult(ok=False, content=f"Error en {self.name}: {e}")

    def as_llm_schema(self) -> dict[str, Any]:
        """Formato de tool calling compatible con LiteLLM (OpenAI style)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema.model_json_schema(),
            },
        }
