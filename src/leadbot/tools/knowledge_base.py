"""Tool: consulta quién es BrainSol y los casos de éxito en knowledge/ para elegir el más parecido al lead."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from leadbot.core.config import KNOWLEDGE_DIR
from leadbot.tools.base import Tool, ToolResult


class CaseStudy(BaseModel):
    name: str
    url: str
    sector: str
    status: str
    fits: list[str]
    body: str


def load_case_studies(knowledge_dir: Path = KNOWLEDGE_DIR) -> list[CaseStudy]:
    cases = []
    for path in sorted((knowledge_dir / "case_studies").glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---", 2)
        cases.append(CaseStudy(**yaml.safe_load(front), body=body.strip()))
    return cases


class KnowledgeArgs(BaseModel):
    sector: str | None = Field(None, description="Clave de sector (ej. comercio)")


class KnowledgeBaseTool(Tool):
    name = "knowledge_base"
    description = (
        "Devuelve los casos de éxito de BrainSol. Si se indica un sector, solo los que encajan con él. "
        "Úsala para elegir qué caso mencionar en el mensaje."
    )
    args_schema = KnowledgeArgs

    def __init__(self, knowledge_dir: Path = KNOWLEDGE_DIR):
        self.knowledge_dir = knowledge_dir

    def run(self, args: KnowledgeArgs) -> ToolResult:
        cases = load_case_studies(self.knowledge_dir)
        if args.sector:
            cases = [c for c in cases if args.sector in c.fits]
        return ToolResult(
            ok=True,
            content="\n\n".join(c.body for c in cases) or "No hay casos para ese sector.",
            data={"cases": [c.name for c in cases]},
        )
