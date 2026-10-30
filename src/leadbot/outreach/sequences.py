"""Seguimientos: si no hay respuesta a los N días (limits.yaml -> sequences), se redacta el siguiente paso.

Los seguimientos también pasan por aprobación (Message.step >= 1 en la pestaña Aprobación).
Una respuesta u opt-out cancela todos los pasos pendientes de ese lead (MessageStatus.CANCELLED).
"""

from sqlalchemy.orm import Session


def schedule_follow_ups(session: Session) -> int:
    """Devuelve cuántos seguimientos nuevos quedaron en borrador."""
    raise NotImplementedError("F3")


def cancel_pending_for_company(session: Session, company_id: int, reason: str) -> int:
    raise NotImplementedError("F3")
