"""WhatsApp Web: abre web.whatsapp.com/send?phone=<E.164> con el mensaje y lo envía. Solo celulares."""

from leadbot.leads.status import Channel
from leadbot.outreach.channels.base import OutgoingMessage, SendResult
from leadbot.outreach.channels.browser_base import BrowserChannel


class WhatsAppWebChannel(BrowserChannel):
    channel = Channel.WHATSAPP

    def send(self, message: OutgoingMessage) -> SendResult:
        raise NotImplementedError("F4")
