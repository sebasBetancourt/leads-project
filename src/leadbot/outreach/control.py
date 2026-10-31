"""Pausas en caliente y alertas.

Las pausas vienen de dos lugares: el `.env` (PAUSE_*) y la tabla `runtime_flags`, que se cambia desde la
pestaña Control de Sheets sin reiniciar el worker. El `.env` manda: si dice pausa, Sheets no la quita.
Las alertas son eventos `alert` que se ven en esa misma pestaña.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadbot.core.clock import from_db, to_db, utcnow
from leadbot.core.config import Settings
from leadbot.leads.models import Event, RuntimeFlag
from leadbot.leads.status import Channel

log = logging.getLogger(__name__)

PAUSE_KEYS = ["pause_all", *(f"pause_{c.value}" for c in Channel)]
TRUE_VALUES = {"true", "si", "sí", "1", "yes"}


def is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def flags(session: Session) -> dict[str, str]:
    return {f.key: f.value for f in session.scalars(select(RuntimeFlag))}


def set_flag(session: Session, key: str, value: bool) -> None:
    row = session.get(RuntimeFlag, key)
    text = "true" if value else "false"
    if row is None:
        session.add(RuntimeFlag(key=key, value=text))
    else:
        row.value = text


def env_forced(settings: Settings, key: str) -> bool:
    """¿El .env tiene esta pausa puesta? (no se puede quitar desde Sheets)."""
    return bool(getattr(settings, key, False))


def pause_reason(session: Session, settings: Settings, channel: Channel) -> str | None:
    """Por qué el canal está en pausa, o None si puede enviar."""
    if settings.is_paused(channel.value):
        return f"PAUSE_{channel.value.upper()} o PAUSE_ALL en .env"
    current = flags(session)
    if is_true(current.get("pause_all")) or is_true(current.get(f"pause_{channel.value}")):
        return "pausa activada desde la pestaña Control"
    return None


def record_alert(session: Session, code: str, text: str, *, dedup_hours: int = 6) -> bool:
    """Registra una alerta (Event `alert`). Si ya hubo una igual hace poco, no se repite. True si es nueva."""
    since = to_db(utcnow() - timedelta(hours=dedup_hours))
    recent = session.scalars(select(Event).where(Event.kind == "alert", Event.created_at >= since))
    if any(e.data.get("code") == code for e in recent):
        return False
    session.add(Event(kind="alert", data={"code": code, "text": text}))
    log.error("ALERTA %s: %s", code, text)
    return True


def recent_alerts(session: Session, limit: int = 10) -> list[tuple[str, str, str]]:
    """(fecha UTC, código, texto) de las últimas alertas."""
    events = session.scalars(select(Event).where(Event.kind == "alert").order_by(Event.id.desc()).limit(limit))
    return [
        (from_db(e.created_at).strftime("%Y-%m-%d %H:%M UTC"), e.data.get("code", ""), e.data.get("text", ""))
        for e in events
    ]


def pause_channel(session: Session, channel: Channel, reason: str) -> None:
    """Pausa un canal solo (lo hacen las protecciones de reputación) y deja una alerta."""
    set_flag(session, f"pause_{channel.value}", True)
    record_alert(session, f"paused:{channel.value}", f"Canal {channel.value} pausado: {reason}")
