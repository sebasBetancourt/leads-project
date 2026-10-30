"""Instagram: DM a cuentas de negocio desde el perfil (recipient = URL del perfil)."""

from leadbot.leads.status import Channel
from leadbot.outreach.channels.base import OutgoingMessage, SendResult
from leadbot.outreach.channels.browser_base import BrowserChannel


class InstagramChannel(BrowserChannel):
    channel = Channel.INSTAGRAM

    def send(self, message: OutgoingMessage) -> SendResult:
        raise NotImplementedError("F4")
