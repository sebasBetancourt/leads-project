"""Revisiones periódicas del worker: lo que debería avisarte antes de que tengas que notarlo tú.

Cada hallazgo es una alerta (Event `alert`, sin repetirse por 6 horas) que se ve en la pestaña Control de Sheets:

- la cuenta de Gmail dejó de aceptar el login  -> además pausa el email solo
- envíos fallidos o a medias (needs_review)
- cola de envío estancada: más de 4 días esperando sin que el canal esté pausado (así un puente no alerta)
- una tarea del worker falló en su última corrida

Un worker muerto no puede avisar de sí mismo: eso lo cubre el healthcheck de docker compose (`leadbot worker-health`)
y la hora de "última sincronización" de la pestaña Control.
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select

from leadbot.core.clock import to_db, utcnow
from leadbot.core.config import get_settings
from leadbot.core.db import session_scope
from leadbot.jobs import heartbeat
from leadbot.leads.models import Job
from leadbot.leads.status import Channel
from leadbot.outreach import control
from leadbot.outreach.sender import gmail_from_settings

log = logging.getLogger(__name__)


def check_gmail_account() -> list[str]:
    """Entra a SMTP e IMAP sin enviar nada. Si falla, pausa el email: mejor parado que con la cuenta bloqueada."""
    problems = gmail_from_settings().check()
    if problems:
        with session_scope() as session:
            control.pause_channel(session, Channel.EMAIL, "; ".join(problems)[:300])
    return problems


def check_worker_health() -> list[str]:
    """Busca problemas en la cola y en el latido del worker. Devuelve los códigos de las alertas nuevas."""
    new: list[str] = []
    with session_scope() as session:
        now = utcnow()

        def alert(code: str, text: str) -> None:
            if control.record_alert(session, code, text):
                new.append(code)

        failed = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "send", Job.status == "failed", Job.created_at >= to_db(now - timedelta(hours=24)))
        )
        if failed:
            alert(
                "send_failed",
                f"{failed} envíos fallaron en las últimas 24 horas. Revisa los mensajes en estado FAILED.",
            )

        stalled = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "send", Job.status == "pending", Job.run_after < to_db(now - timedelta(days=4)))
        )
        if stalled and not control.pause_reason(session, get_settings(), Channel.EMAIL):
            alert(
                "send_stalled", f"{stalled} emails llevan más de 4 días esperando salir sin que el canal esté pausado."
            )

        review = session.scalar(select(func.count()).select_from(Job).where(Job.status == "needs_review"))
        if review:
            alert("send_review", f"{review} envíos quedaron a medias. Corre `leadbot send --reconcile`.")

        for beat in heartbeat.all_heartbeats():
            if beat.ok is False:
                alert(f"task_failed:{beat.task}", f"La tarea {beat.task} falló en su última corrida: {beat.detail}")
    return new
