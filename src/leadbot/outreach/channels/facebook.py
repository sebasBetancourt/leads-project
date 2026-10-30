"""Facebook: mensaje a la página del negocio por Messenger (recipient = URL de la página)."""

from leadbot.leads.status import Channel
from leadbot.outreach.channels.base import OutgoingMessage, SendResult
from leadbot.outreach.channels.browser_base import BrowserChannel


class FacebookChannel(BrowserChannel):
    channel = Channel.FACEBOOK

    def send(self, message: OutgoingMessage) -> SendResult:
        raise NotImplementedError("F4")
