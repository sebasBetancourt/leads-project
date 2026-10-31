"""Tiempo en un solo lugar. Las columnas datetime de Postgres son sin zona y guardan UTC."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Ahora, con zona UTC (para comparar con horarios locales)."""
    return datetime.now(UTC)


def to_db(moment: datetime) -> datetime:
    """UTC sin zona: como se guarda en las columnas `datetime`."""
    return moment.astimezone(UTC).replace(tzinfo=None)


def from_db(moment: datetime) -> datetime:
    """Lo que viene de una columna `datetime` (UTC sin zona) -> con zona UTC."""
    return moment.replace(tzinfo=UTC)
