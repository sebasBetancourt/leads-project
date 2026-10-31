"""Envío de email (F3): de un mensaje APROBADO a un correo enviado, con todas las protecciones. ADR 0007.

    aprobar en Sheets ─► enqueue_approved: mensaje QUEUED + trabajo `send` (dedup_key = id del mensaje)
    cada minuto       ─► EmailSender.run: puertas globales (pausa, horario, tope diario, ritmo)
                         ─► toma un trabajo ─► revisa al destinatario ─► guarda el Message-ID ─► SMTP ─► SENT

Garantía contra envíos dobles: el Message-ID se guarda ANTES de abrir la conexión. Si el worker muere a mitad
de un envío, el trabajo queda `needs_review` (nunca se reintenta solo) y `reconcile` busca ese Message-ID en la
carpeta Enviados: si está, el mensaje salió; si no, se reencola. Prefiere que un email no salga a que salga dos veces.

Las puertas globales se revisan antes de tomar el trabajo: lo que no puede salir ahora simplemente espera en la
cola, sin gastar intentos. Solo los errores de SMTP gastan intentos.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadbot.core.clock import to_db, utcnow
from leadbot.core.config import Settings, get_settings
from leadbot.core.db import session_scope
from leadbot.leads.models import Company, Event, Job, Message
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import Channel, LeadStatus, MessageStatus
from leadbot.outreach import control, queue
from leadbot.outreach.channels.base import OutgoingMessage, SendResult
from leadbot.outreach.channels.email_gmail import GmailChannel
from leadbot.outreach.guardrails import Decision, SendPolicy, check_send, load_policy, next_window_start, pacing_delay

log = logging.getLogger(__name__)

SEND_JOB = "send"
SENDABLE_LEADS = (LeadStatus.APPROVED, LeadStatus.CONTACTED)  # APPROVED: primer contacto · CONTACTED: seguimientos


@dataclass
class CycleStats:
    enqueued: int = 0
    sent: int = 0
    cancelled: int = 0
    failed: int = 0
    retry_later: int = 0
    blocked: str = ""  # por qué no se pudo enviar ahora (vacío = no hubo bloqueo)
    next_try: datetime | None = None
    would_send: list[str] = field(default_factory=list)  # solo en dry_run


def gmail_from_settings(settings: Settings | None = None) -> GmailChannel:
    settings = settings or get_settings()
    if not (settings.gmail_address and settings.gmail_app_password):
        raise RuntimeError("Faltan GMAIL_ADDRESS y GMAIL_APP_PASSWORD en .env")
    return GmailChannel(
        settings.gmail_address,
        settings.gmail_app_password,
        settings.sender_name,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        imap_host=settings.imap_host,
    )


def enqueue_approved(session: Session) -> int:
    """Pasa a QUEUED los emails APROBADOS (con un trabajo `send` cada uno). Se puede llamar las veces que sea."""
    approved = session.scalars(
        select(Message)
        .join(Company)
        .where(
            Message.channel == Channel.EMAIL,
            Message.status == MessageStatus.APPROVED,
            Company.status.in_(SENDABLE_LEADS),
        )
        .order_by(Message.step, Message.id)
    )
    count = 0
    for message in approved:
        queue.enqueue(session, SEND_JOB, {"message_id": message.id}, dedup_key=str(message.id))
        message.status = MessageStatus.QUEUED
        session.add(Event(company_id=message.company_id, message_id=message.id, kind="queued", data={}))
        count += 1
    return count


class EmailSender:
    def __init__(self, adapter: GmailChannel, policy: SendPolicy | None = None, settings: Settings | None = None):
        self.adapter = adapter
        self.policy = policy or load_policy()
        self.settings = settings or get_settings()
        self.limits = self.policy.channels[Channel.EMAIL]

    # --- Puertas globales ----------------------------------------------------------------

    def gate(self, session: Session, now: datetime) -> tuple[Decision, datetime | None]:
        """¿Puede salir un email ahora? Devuelve la decisión y cuándo conviene volver a intentar."""
        if reason := control.pause_reason(session, self.settings, Channel.EMAIL):
            return Decision(False, f"canal email en pausa ({reason})"), None
        repo = LeadRepository(session)
        tz = self.policy.timezone
        decision = check_send(
            self.policy,
            Channel.EMAIL,
            now=now,
            sent_today=repo.sent_today(Channel.EMAIL, now, tz),
            days_active=repo.days_active(Channel.EMAIL, now, tz),
            paused=False,
            suppressed=False,
            body="",
        )
        if not decision.allowed:
            if "tope" in decision.reason:  # el cupo de hoy se llenó: se sigue en la próxima ventana de mañana
                return decision, next_window_start(self.policy, _tomorrow(now, tz))
            return decision, next_window_start(self.policy, now)
        if last := repo.last_sent(Channel.EMAIL):
            ready = last[1] + timedelta(seconds=pacing_delay(self.limits, last[0]))
            if now < ready:
                return Decision(False, "pausa entre envíos"), ready
        return Decision(True), None

    # --- Ciclo ---------------------------------------------------------------------------

    def run(self, max_messages: int = 1, *, dry_run: bool = False, now: datetime | None = None) -> CycleStats:
        stats = CycleStats()
        with session_scope() as session:
            quarantined = queue.quarantine_stale(session)
            for job in quarantined:
                control.record_alert(
                    session,
                    f"send_review:{job.id}",
                    f"El envío del mensaje {job.payload.get('message_id')} quedó a medias. "
                    "Corre `leadbot send --reconcile` para confirmar si salió.",
                )
            if not dry_run:
                stats.enqueued = enqueue_approved(session)

        if dry_run:  # lo que saldría, aunque ahora mismo una puerta lo bloquee
            stats.would_send = self._preview(max_messages)
            with session_scope() as session:
                decision, stats.next_try = self.gate(session, now or utcnow())
            stats.blocked = "" if decision.allowed else decision.reason
            return stats

        for _ in range(max_messages):
            moment = now or utcnow()
            with session_scope() as session:
                decision, retry_at = self.gate(session, moment)
            if not decision.allowed:
                stats.blocked, stats.next_try = decision.reason, retry_at
                break
            if not self._send_one(stats, moment):
                break
        return stats

    def _preview(self, limit: int) -> list[str]:
        with session_scope() as session:
            rows = session.execute(
                select(Message, Company.name)
                .join(Company)
                .where(
                    Message.channel == Channel.EMAIL, Message.status.in_([MessageStatus.APPROVED, MessageStatus.QUEUED])
                )
                .order_by(Message.step, Message.id)
                .limit(limit)
            )
            return [f"#{m.id} {name} → {m.contact.value}: {m.subject}" for m, name in rows]

    def _send_one(self, stats: CycleStats, now: datetime) -> bool:
        """Toma un trabajo y lo envía. False si no había ninguno."""
        prepared = self._claim(stats, now)
        if prepared is None:
            return False
        if isinstance(prepared, bool):
            return prepared  # el trabajo se resolvió sin enviar (cancelado); hay que seguir buscando
        job_id, outgoing, context = prepared
        result = self.adapter.send(outgoing)  # sin transacción abierta
        self._record(job_id, outgoing, context, result, stats, now)
        return True

    def _claim(self, stats: CycleStats, now: datetime):
        """Fase 1 (una transacción): toma el trabajo, revisa al destinatario y guarda el Message-ID."""
        with session_scope() as session:
            job = queue.claim_next(session, SEND_JOB)
            if job is None:
                return None
            repo = LeadRepository(session)
            message = session.get(Message, job.payload["message_id"], with_for_update=True)
            ctx = {"company_id": message.company_id if message else None, "message_id": job.payload["message_id"]}
            ctx["job_id"] = job.id
            if message is None or message.status not in (MessageStatus.QUEUED, MessageStatus.APPROVED):
                queue.complete(session, job)  # ya se resolvió por otro camino (cancelado, enviado)
                return True
            company = session.get(Company, message.company_id)
            reason = self._blocked_reason(repo, message, company)
            if reason:
                self._cancel(session, repo, job, message, reason)
                stats.cancelled += 1
                log.info("envío cancelado: %s", reason, extra=ctx)
                return True
            if message.external_id is None:
                message.external_id = self.adapter.new_message_id()
            message.status = MessageStatus.QUEUED
            thread = repo.thread_ids(message)
            outgoing = OutgoingMessage(
                message_id=message.id,
                recipient=message.contact.value,
                subject=message.subject,
                body=message.body,
                attachment_path=message.attachment_path,
                external_id=message.external_id,
                in_reply_to=thread[-1] if thread else None,
                references=thread,
            )
            return job.id, outgoing, ctx

    @staticmethod
    def _blocked_reason(repo: LeadRepository, message: Message, company: Company) -> str | None:
        recipient = message.contact
        if repo.is_suppressed(recipient.value):
            return "destinatario en lista de no contactar"
        if recipient.invalid:
            return "la dirección rebotó antes"
        if company.status not in SENDABLE_LEADS:
            return f"el lead está en {company.status.value}"
        return None

    @staticmethod
    def _cancel(session: Session, repo: LeadRepository, job: Job, message: Message, reason: str) -> None:
        message.status = MessageStatus.CANCELLED
        session.add(
            Event(company_id=message.company_id, message_id=message.id, kind="send_blocked", data={"reason": reason})
        )
        queue.complete(session, job)

    def _record(
        self, job_id: int, out: OutgoingMessage, ctx: dict, result: SendResult, stats: CycleStats, now: datetime
    ) -> None:
        """Fase 2 (otra transacción): deja constancia del resultado."""
        with session_scope() as session:
            job = session.get(Job, job_id)
            message = session.get(Message, out.message_id, with_for_update=True)
            company = session.get(Company, message.company_id, with_for_update=True)
            repo = LeadRepository(session)
            if result.ok:
                message.status, message.sent_at, message.error = MessageStatus.SENT, to_db(now), None
                session.add(
                    Event(
                        company_id=company.id,
                        message_id=message.id,
                        kind="sent",
                        data={"to": out.recipient, "external_id": out.external_id},
                    )
                )
                if company.status == LeadStatus.APPROVED:
                    repo.change_status(company, LeadStatus.CONTACTED, "primer email enviado")
                queue.complete(session, job)
                stats.sent += 1
                log.info("email enviado a %s", out.recipient, extra=ctx)
                return

            message.error = (result.error or "")[:2000]
            session.add(
                Event(
                    company_id=company.id,
                    message_id=message.id,
                    kind="send_failed",
                    data={"error": message.error, "retryable": result.retryable},
                )
            )
            if result.pause_channel:
                control.pause_channel(session, Channel.EMAIL, result.error or "error de cuenta")
            if result.bad_recipient:
                message.contact.invalid = True
                repo.add_suppression(out.recipient, "el servidor rechazó la dirección")
            if result.pause_channel:
                queue.defer(job)  # el problema es de la cuenta (login, cuota), no del mensaje
                stats.retry_later += 1
            elif result.retryable:
                queue.fail(session, job, message.error, retryable=True)
                if job.status == "failed":
                    message.status = MessageStatus.FAILED
                    stats.failed += 1
                else:
                    stats.retry_later += 1
            else:
                queue.fail(session, job, message.error, retryable=False)
                message.status = MessageStatus.FAILED
                stats.failed += 1
            log.warning("email a %s falló: %s", out.recipient, message.error, extra=ctx)


def _tomorrow(now: datetime, tz) -> datetime:
    local = now.astimezone(tz)
    return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(now.tzinfo)


@dataclass
class ReconcileStats:
    sent: int = 0  # estaban en Enviados: el mensaje sí salió
    requeued: int = 0  # no estaban y se pidió reencolar (--requeue)
    still_in_review: int = 0  # no estaban y no se pidió reencolar: decide una persona


def reconcile(adapter: GmailChannel, *, requeue: bool = False, dry_run: bool = False) -> ReconcileStats:
    """Resuelve los envíos que quedaron a medias (trabajos needs_review) mirando la carpeta Enviados.

    Si el Message-ID está en Enviados, el mensaje salió. Si no está, NO se reencola solo: Gmail podría haber
    reescrito el Message-ID, y reenviar sería el error caro. Con `requeue=True` una persona confirma que no salió.
    """
    stats = ReconcileStats()
    with session_scope() as session:
        jobs = list(session.scalars(select(Job).where(Job.kind == SEND_JOB, Job.status == "needs_review")))
        if not jobs:
            return stats
        in_sent = adapter.sent_message_ids()
        for job in jobs:
            message = session.get(Message, job.payload["message_id"], with_for_update=True)
            company = session.get(Company, message.company_id)
            if message.external_id in in_sent:
                stats.sent += 1
                if not dry_run:
                    message.status, message.sent_at = MessageStatus.SENT, message.sent_at or to_db(utcnow())
                    if company.status == LeadStatus.APPROVED:
                        LeadRepository(session).change_status(
                            company, LeadStatus.CONTACTED, "primer email (reconciliado)"
                        )
                    session.add(
                        Event(company_id=company.id, message_id=message.id, kind="sent", data={"reconciled": True})
                    )
                    queue.complete(session, job)
            elif requeue:
                stats.requeued += 1
                if not dry_run:
                    message.status = MessageStatus.QUEUED
                    job.status, job.locked_at = "pending", None
                    session.add(Event(company_id=company.id, message_id=message.id, kind="send_requeued", data={}))
            else:
                stats.still_in_review += 1
    return stats


def run_send_cycle(max_messages: int = 1, *, dry_run: bool = False) -> CycleStats:
    """Lo que corre el worker cada minuto."""
    return EmailSender(gmail_from_settings()).run(max_messages, dry_run=dry_run)
