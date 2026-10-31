"""Seguimientos (F3): los trabajos `followup` que deja outreach/sequences.py se convierten en borradores.

    sequences.schedule_follow_ups ─► trabajo `followup` ─► grafo draft_follow_up → save_follow_up ─► PENDING_APPROVAL

El agente reutiliza la investigación guardada del lead (no vuelve a buscar) y redacta solo el cuerpo: el asunto
("Re: ...") lo arma el código para que el seguimiento quede en el mismo hilo.
"""

import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from leadbot.agents.graph import build_follow_up_graph
from leadbot.agents.pipeline import AgentStats, lead_context
from leadbot.agents.schemas import LeadQualification
from leadbot.agents.state import AgentState, FollowUpContext
from leadbot.core.db import session_scope
from leadbot.core.llm import LLMError
from leadbot.leads.models import Company, ContactChannel, Job, Message
from leadbot.leads.repository import LeadRepository, LeadStateChanged
from leadbot.leads.status import LeadStatus, MessageStatus
from leadbot.outreach import queue, sequences

log = logging.getLogger(__name__)


def reply_subject(original: str | None) -> str:
    base = re.sub(r"^((re|rv):\s*)+", "", (original or "").strip(), flags=re.I)
    return f"Re: {base}" if base else "Re: seguimiento"


def follow_up_state(payload: dict) -> AgentState:
    """Estado de entrada del grafo para un trabajo `followup`."""
    company_id, contact_id, step = payload["company_id"], payload["contact_id"], payload["step"]
    redo_id = payload.get("redo_message_id")
    with session_scope() as session:
        repo = LeadRepository(session)
        company = session.get(Company, company_id, options=[selectinload(Company.contacts)])
        contact = session.get(ContactChannel, contact_id)
        if company is None or contact is None or company.status != LeadStatus.CONTACTED:
            raise LeadStateChanged(f"lead {company_id}: ya no está CONTACTED")
        research = repo.latest_research(company_id)
        if research is None or "qualification" not in research.data:
            raise LeadStateChanged(f"lead {company_id}: no hay investigación guardada")
        sent = list(
            session.scalars(
                select(Message)
                .where(
                    Message.company_id == company_id,
                    Message.contact_id == contact_id,
                    Message.step < step,
                    Message.status == MessageStatus.SENT,
                )
                .order_by(Message.step)
            )
        )
        if not sent:
            raise LeadStateChanged(f"lead {company_id}: no hay un mensaje anterior enviado")
        feedback = []
        if redo_id is not None and (redo := session.get(Message, redo_id)) and redo.reviewer_notes:
            feedback = [redo.reviewer_notes]
        follow_up = FollowUpContext(
            step=step,
            contact_id=contact_id,
            recipient=contact.value,
            subject=reply_subject(sent[0].subject),
            previous=[m.body for m in sent],
            redo_message_id=redo_id,
        )
        return AgentState(
            lead=lead_context(repo, company),
            research_notes=research.data.get("research_notes", []),
            signals=research.data.get("signals", {}),
            qualification=LeadQualification.model_validate(research.data["qualification"]),
            research_id=research.id,
            feedback=feedback,
            follow_up=follow_up,
            usage=[],
        )


class FollowUpAgent:
    def __init__(self, *, dry_run: bool = False):
        self.graph = build_follow_up_graph(dry_run=dry_run)

    def run(self, payload: dict) -> AgentState:
        return self.graph.invoke(follow_up_state(payload))


def process_follow_ups(max_jobs: int = 20, agent: FollowUpAgent | None = None) -> AgentStats:
    stats = AgentStats()
    agent = agent or FollowUpAgent()
    for _ in range(max_jobs):
        with session_scope() as session:
            job = queue.claim_next(session, sequences.FOLLOW_UP_JOB)
            claimed = (job.id, dict(job.payload)) if job else None
        if claimed is None:
            break
        job_id, payload = claimed
        error, retryable = None, True
        try:
            final = agent.run(payload)
        except LeadStateChanged as e:
            log.info("seguimiento: %s", e)
            stats.skipped += 1
        except LLMError as e:
            log.warning("seguimiento: lead %d falló en el LLM: %s", payload["company_id"], e)
            error = str(e)
        except Exception as e:
            log.exception("seguimiento: lead %d falló", payload["company_id"])
            error = f"{type(e).__name__}: {e}"
        else:
            stats.processed += 1
            usage = final.get("usage", [])
            stats.tokens += sum(u["prompt_tokens"] + u["completion_tokens"] for u in usage)
            stats.cost_usd += sum(u["cost_usd"] for u in usage)
        if error:
            stats.failed += 1
        with session_scope() as session:
            job = session.get(Job, job_id)
            if error is None:
                queue.complete(session, job)
            else:
                queue.fail(session, job, error, retryable=retryable)
    return stats


def run_follow_ups(limit: int = 20, now: datetime | None = None) -> AgentStats:
    """Cierra las secuencias terminadas, encola lo que toca y redacta. Lo que corre el worker."""
    with session_scope() as session:
        closed = sequences.close_finished_sequences(session, now)
        scheduled = sequences.schedule_follow_ups(session, now)
    stats = process_follow_ups(max_jobs=limit)
    stats.enqueued = scheduled
    log.info("seguimientos: %d encolados, %d leads cerrados sin respuesta", scheduled, closed)
    return stats
