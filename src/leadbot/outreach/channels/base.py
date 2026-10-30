"""Contrato de un canal de envío. Cada red es un adaptador intercambiable (patrón de AgentSDR)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from leadbot.leads.status import Channel


@dataclass
class OutgoingMessage:
    message_id: int
    recipient: str  # email, +57..., o URL del perfil
    body: str
    subject: str | None = None
    attachment_path: str | None = None


@dataclass
class SendResult:
    ok: bool
    external_id: str | None = None
    error: str | None = None
    retryable: bool = False  # True: error temporal (red). False: no reintentar (perfil no existe, bloqueo)


class ChannelAdapter(ABC):
    channel: Channel
    runs_in_docker: bool = True  # los canales con navegador corren en tu máquina (docs/adr/0005)

    @abstractmethod
    def send(self, message: OutgoingMessage) -> SendResult: ...

    def health_check(self) -> bool:
        """¿La sesión o credencial sigue activa? Si no, el canal se pausa solo."""
        return True
