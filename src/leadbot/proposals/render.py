"""Propuesta comercial en PDF por lead: Jinja2 (HTML) -> WeasyPrint (PDF). Extra opcional [pdf]."""

from pathlib import Path

from leadbot.agents.schemas import LeadQualification
from leadbot.core.config import OUTPUT_DIR

TEMPLATES_DIR = Path(__file__).parent / "templates"


def render_proposal(
    company_name: str,
    qualification: LeadQualification,
    *,
    output_dir: Path = OUTPUT_DIR / "proposals",
) -> Path:
    raise NotImplementedError("F5")
