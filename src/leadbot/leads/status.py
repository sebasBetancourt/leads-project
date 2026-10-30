"""Máquina de estados del lead y del mensaje. Ningún módulo cambia un estado sin pasar por aquí."""

from enum import StrEnum


class LeadStatus(StrEnum):
    NEW = "NEW"                            # recién descubierto en Google Maps
    ENRICHED = "ENRICHED"                  # ya tiene canales de contacto de su web
    QUALIFIED = "QUALIFIED"                # el agente lo calificó como buen prospecto
    UNQUALIFIED = "UNQUALIFIED"
    DRAFTED = "DRAFTED"                    # mensajes redactados
    PENDING_APPROVAL = "PENDING_APPROVAL"  # esperando tu aprobación en Sheets
    APPROVED = "APPROVED"
    CONTACTED = "CONTACTED"                # al menos un mensaje enviado
    REPLIED = "REPLIED"
    MEETING = "MEETING"
    WON = "WON"
    LOST = "LOST"
    OPT_OUT = "OPT_OUT"                    # pidió no ser contactado: final e irreversible


LEAD_TRANSITIONS: dict[LeadStatus, set[LeadStatus]] = {
    LeadStatus.NEW: {LeadStatus.ENRICHED, LeadStatus.UNQUALIFIED},
    LeadStatus.ENRICHED: {LeadStatus.QUALIFIED, LeadStatus.UNQUALIFIED},
    LeadStatus.QUALIFIED: {LeadStatus.DRAFTED},
    LeadStatus.UNQUALIFIED: {LeadStatus.ENRICHED},  # re-evaluar si cambian los criterios
    LeadStatus.DRAFTED: {LeadStatus.PENDING_APPROVAL},
    LeadStatus.PENDING_APPROVAL: {
        LeadStatus.APPROVED,
        LeadStatus.DRAFTED,       # rechazado con comentarios: se vuelve a redactar
        LeadStatus.UNQUALIFIED,   # rechazado: no es buen prospecto
    },
    LeadStatus.APPROVED: {LeadStatus.CONTACTED},
    LeadStatus.CONTACTED: {LeadStatus.REPLIED, LeadStatus.LOST},
    LeadStatus.REPLIED: {LeadStatus.MEETING, LeadStatus.LOST},
    LeadStatus.MEETING: {LeadStatus.WON, LeadStatus.LOST},
    LeadStatus.WON: set(),
    LeadStatus.LOST: set(),
    LeadStatus.OPT_OUT: set(),
}


class MessageStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"  # el lead respondió u optó por salir antes del envío


class Channel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"


class InvalidTransition(ValueError):
    pass


def can_transition(current: LeadStatus, target: LeadStatus) -> bool:
    if target == LeadStatus.OPT_OUT:
        return current != LeadStatus.OPT_OUT
    return target in LEAD_TRANSITIONS[current]


def ensure_transition(current: LeadStatus, target: LeadStatus) -> LeadStatus:
    if not can_transition(current, target):
        raise InvalidTransition(f"{current} -> {target} no está permitido")
    return target
