"""Email por Gmail: SMTP (smtp.gmail.com:465) con contraseña de aplicación. Respuestas por IMAP."""

from leadbot.leads.status import Channel
from leadbot.outreach.channels.base import ChannelAdapter, OutgoingMessage, SendResult


class GmailChannel(ChannelAdapter):
    channel = Channel.EMAIL

    def __init__(self, address: str, app_password: str, sender_name: str):
        self.address = address
        self.app_password = app_password
        self.sender_name = sender_name

    def send(self, message: OutgoingMessage) -> SendResult:
        """Incluye cabecera List-Unsubscribe y adjunta la propuesta si existe."""
        raise NotImplementedError("F3")

    def fetch_replies(self, since_days: int = 14) -> list[dict]:
        """Lee INBOX por IMAP y empareja respuestas con mensajes enviados (Message-ID / In-Reply-To)."""
        raise NotImplementedError("F3")
