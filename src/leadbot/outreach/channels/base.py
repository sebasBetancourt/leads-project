"""Contrato de un canal con envío automático (hoy solo email). Patrón de adaptadores de AgentSDR.

Las redes sociales no usan este contrato: son asistidas (channels/assisted.py, docs/adr/0005).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from leadbot.leads.status import Channel


@dataclass
class OutgoingMessage:
    message_id: int
    recipient: str  # email
    body: str
    subject: str | None = None
    attachment_path: str | None = None
    external_id: str | None = None  # Message-ID: se genera y se guarda ANTES de enviar (ADR 0007)
    in_reply_to: str | None = None  # seguimientos: Message-ID del mensaje anterior del hilo
    references: list[str] = field(default_factory=list)  # todos los anteriores del hilo


@dataclass
class SendResult:
    ok: bool
    external_id: str | None = None
    error: str | None = None
    retryable: bool = False  # True: error temporal (red). False: no reintentar (buzón no existe, bloqueo)
    pause_channel: bool = False  # credencial inválida o cuota diaria: el canal se pausa, el mensaje no se pierde
    bad_recipient: bool = False  # el servidor rechazó al destinatario: la dirección no sirve


class ChannelAdapter(ABC):
    channel: Channel

    @abstractmethod
    def send(self, message: OutgoingMessage) -> SendResult: ...

    def health_check(self) -> bool:
        """¿La credencial sigue activa? Si no, el canal se pausa solo."""
        return True
