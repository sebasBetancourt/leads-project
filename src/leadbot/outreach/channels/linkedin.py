"""LinkedIn: solicitud de conexión con nota (máx. 300 caracteres) a la página o al dueño."""

from leadbot.leads.status import Channel
from leadbot.outreach.channels.base import OutgoingMessage, SendResult
from leadbot.outreach.channels.browser_base import BrowserChannel


class LinkedInChannel(BrowserChannel):
    channel = Channel.LINKEDIN

    def send(self, message: OutgoingMessage) -> SendResult:
        raise NotImplementedError("F4")
