"""Números por día para la pestaña Métricas, calculados desde la bitácora (`events`)."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadbot.core.clock import from_db, to_db
from leadbot.leads.models import Event

KINDS = {"sent": "enviados", "replied": "respuestas", "opted_out": "bajas", "bounced": "rebotes"}


@dataclass
class DayMetrics:
    day: date
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS.values(), 0))


def daily_metrics(session: Session, now: datetime, tz: ZoneInfo, days: int = 30) -> list[DayMetrics]:
    """Los últimos `days` días (el más reciente primero), con los días sin actividad en cero."""
    today = now.astimezone(tz).date()
    rows = {today - timedelta(days=i): DayMetrics(today - timedelta(days=i)) for i in range(days)}
    since = to_db(now - timedelta(days=days + 1))
    for event in session.scalars(select(Event).where(Event.kind.in_(KINDS), Event.created_at >= since)):
        local = from_db(event.created_at).astimezone(tz).date()
        if local in rows:
            rows[local].counts[KINDS[event.kind]] += 1
    return sorted(rows.values(), key=lambda m: m.day, reverse=True)


def metrics_grid(metrics: list[DayMetrics]) -> list[list]:
    """Encabezado, una fila por día y una fila de totales con la tasa de respuesta."""
    columns = list(KINDS.values())
    grid: list[list] = [["fecha", *columns]]
    grid += [[m.day.isoformat(), *(m.counts[c] for c in columns)] for m in metrics]
    totals = {c: sum(m.counts[c] for m in metrics) for c in columns}
    rate = f"{100 * totals['respuestas'] / totals['enviados']:.1f}%" if totals["enviados"] else "—"
    grid.append(["TOTAL", *(totals[c] for c in columns)])
    grid.append(["tasa de respuesta", rate, "", "", ""])
    return grid
