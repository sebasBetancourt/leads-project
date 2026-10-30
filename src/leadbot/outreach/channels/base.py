"""Contrato de un canal con envío automático (hoy solo email). Patrón de adaptadores de AgentSDR.

Las redes sociales no usan este contrato: son asistidas (channels/assisted.py, docs/adr/0005).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from leadbot.leads.status import Channel


@dataclass
class OutgoingMessage:
    message_id: int
    recipient: str  # email
    body: str
    subject: str | None = None
    attachment_path: str | None = None


@dataclass
class SendResult:
    ok: bool
    external_id: str | None = None
    error: str | None = None
    retryable: bool = False  # True: error temporal (red). False: no reintentar (buzón no existe, bloqueo)


class ChannelAdapter(ABC):
    channel: Channel

    @abstractmethod
    def send(self, message: OutgoingMessage) -> SendResult: ...

    def health_check(self) -> bool:
        """¿La credencial sigue activa? Si no, el canal se pausa solo."""
        return True
