"""Etapa 3: leads ENRICHED -> agente (investiga, califica, redacta) -> PENDING_APPROVAL o UNQUALIFIED.

Cada lead es un trabajo de la cola (outreach/queue.py), así el worker y `leadbot qualify` pueden
correr a la vez sin procesar dos veces el mismo lead. Los leads DRAFTED (REHACER en Sheets) se
vuelven a redactar con el grafo corto, reutilizando su investigación.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import selectinload

from leadbot.agents.graph import build_lead_graph, build_redraft_graph
from leadbot.agents.schemas import LeadQualification
from leadbot.agents.state import AgentState, LeadContext
from leadbot.core.db import session_scope
from leadbot.core.llm import LLMError
from leadbot.leads.models import Company, Job
from leadbot.leads.repository import LeadRepository, LeadStateChanged
from leadbot.leads.status import LeadStatus
from leadbot.outreach import queue
from leadbot.tools.base import Tool
from leadbot.tools.registry import default_tools

log = logging.getLogger(__name__)

QUALIFY_JOB = "qualify"
REDRAFT_JOB = "redraft"


@dataclass
class AgentStats:
    enqueued: int = 0
    processed: int = 0
    qualified: int = 0
    unqualified: int = 0
    redrafted: int = 0
    skipped: int = 0
    failed: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


def lead_context(repo: LeadRepository, company: Company) -> LeadContext:
    return LeadContext(
        company_id=company.id,
        name=company.name,
        sector=company.sector,
        city=company.city,
        website=company.website,
        google_category=company.google_category,
        rating=company.rating,
        reviews_count=company.reviews_count,
        channels=repo.contactable_channels(company),
    )


def initial_state(company_id: int) -> tuple[AgentState, bool]:
    """Estado de entrada del grafo y si es para volver a redactar."""
    with session_scope() as session:
        repo = LeadRepository(session)
        company = session.get(Company, company_id, options=[selectinload(Company.contacts)])
        if company is None:
            raise LeadStateChanged(f"lead {company_id} no existe")
        state = AgentState(lead=lead_context(repo, company), research_notes=[], usage=[])
        if company.status == LeadStatus.ENRICHED:
            return state, False
        if company.status != LeadStatus.DRAFTED:
            raise LeadStateChanged(f"lead {company_id} está en {company.status}: no hay nada que hacer")

        research = repo.latest_research(company_id)
        to_redraft = repo.messages_to_redraft(company_id)
        if research is None or not to_redraft:
            raise LeadStateChanged(f"lead {company_id}: DRAFTED sin investigación o sin mensajes para rehacer")
        state.update(
            qualification=LeadQualification.model_validate(research.data["qualification"]),
            research_notes=research.data.get("research_notes", []),
            signals=research.data.get("signals", {}),
            research_id=research.id,
            redraft_channels=[m.channel for m in to_redraft],
            feedback=[m.reviewer_notes for m in to_redraft if m.reviewer_notes],
        )
        return state, True


class LeadAgent:
    """Compila los grafos una vez por proceso y procesa leads de a uno."""

    def __init__(self, tools: dict[str, Tool] | None = None, *, dry_run: bool = False):
        tools = tools if tools is not None else default_tools()
        self.dry_run = dry_run
        self.full = build_lead_graph(tools, dry_run=dry_run)
        self.redraft = build_redraft_graph(tools, dry_run=dry_run)

    def run(self, company_id: int) -> AgentState:
        state, is_redraft = initial_state(company_id)
        return (self.redraft if is_redraft else self.full).invoke(state)


def enqueue_ready(limit: int = 50) -> int:
    """Encola los leads ENRICHED (calificar) y DRAFTED (volver a redactar). Sin duplicados."""
    count = 0
    with session_scope() as session:
        repo = LeadRepository(session)
        for status, kind in ((LeadStatus.ENRICHED, QUALIFY_JOB), (LeadStatus.DRAFTED, REDRAFT_JOB)):
            for company in repo.fetch_by_status(status, limit=limit):
                count += queue.enqueue(session, kind, {"company_id": company.id}, dedup_key=str(company.id)) is not None
    return count


def _claim() -> tuple[int, str, int] | None:
    with session_scope() as session:
        for kind in (REDRAFT_JOB, QUALIFY_JOB):  # primero lo que ya revisaste: te lo devuelve antes
            if job := queue.claim_next(session, kind):
                return job.id, job.kind, job.payload["company_id"]
    return None


def _finish(job_id: int, error: str | None = None, *, retryable: bool = True) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if error is None:
            queue.complete(session, job)
        else:
            queue.fail(session, job, error, retryable=retryable)


def process_queue(max_jobs: int = 20, agent: LeadAgent | None = None) -> AgentStats:
    stats = AgentStats()
    with session_scope() as session:
        if released := queue.release_stale(session):
            log.warning("agente: %d trabajos colgados vuelven a la cola", released)

    agent = agent or LeadAgent()
    for _ in range(max_jobs):
        claimed = _claim()
        if claimed is None:
            break
        job_id, kind, company_id = claimed
        try:
            final = agent.run(company_id)
        except LeadStateChanged as e:
            log.info("agente: %s", e)
            stats.skipped += 1
            _finish(job_id)
            continue
        except LLMError as e:  # red, cuota del proveedor o salida inválida: se reintenta más tarde
            log.warning("agente: lead %d falló en el LLM: %s", company_id, e)
            stats.failed += 1
            _finish(job_id, str(e))
            continue
        except Exception as e:
            log.exception("agente: lead %d falló", company_id)
            stats.failed += 1
            _finish(job_id, f"{type(e).__name__}: {e}")
            continue

        _finish(job_id)
        stats.processed += 1
        _count(stats, final, redraft=kind == REDRAFT_JOB)
    return stats


def _count(stats: AgentStats, final: AgentState, *, redraft: bool) -> None:
    usage = final.get("usage", [])
    stats.tokens += sum(u["prompt_tokens"] + u["completion_tokens"] for u in usage)
    stats.cost_usd += sum(u["cost_usd"] for u in usage)
    if redraft:
        stats.redrafted += 1
    elif final["qualification"].qualified:
        stats.qualified += 1
    else:
        stats.unqualified += 1
    q = final["qualification"]
    log.info(
        "%-40s score %3d %s %s",
        final["lead"]["name"][:40],
        q.score,
        "✓" if q.qualified else "✗",
        [d.channel.value for d in final.get("drafts", [])],
    )


def run_agent(limit: int = 20) -> AgentStats:
    """Encola lo pendiente y procesa hasta `limit` trabajos (lo que usan el CLI y el worker)."""
    enqueued = enqueue_ready(limit=max(limit, 50))
    stats = process_queue(max_jobs=limit)
    stats.enqueued = enqueued
    return stats
