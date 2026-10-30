"""Canales asistidos: WhatsApp, Instagram, Facebook y LinkedIn (ver docs/adr/0005).

El sistema no envía nada por estas redes. Arma el enlace que abre el chat con ese negocio y lo
pone en Sheets junto al mensaje aprobado; tú das el clic, envías y marcas ENVIADO. Así no se
automatiza tu cuenta y no hay riesgo de bloqueo por bots.
"""

from abc import ABC, abstractmethod
from typing import ClassVar
from urllib.parse import quote, urlparse

from leadbot.leads.status import Channel


def handle_from_url(url: str) -> str:
    """'https://www.instagram.com/motos.bga/?hl=es' -> 'motos.bga'."""
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else ""


class AssistedChannel(ABC):
    channel: ClassVar[Channel]
    prefills_text: ClassVar[bool] = False  # True: el enlace ya trae el mensaje escrito

    @abstractmethod
    def chat_link(self, recipient: str, body: str) -> str:
        """Enlace que abre la conversación con el negocio."""


class WhatsAppAssisted(AssistedChannel):
    channel = Channel.WHATSAPP
    prefills_text = True

    def chat_link(self, recipient: str, body: str) -> str:
        """recipient en E.164 (+573001234567). wa.me abre WhatsApp con el texto escrito."""
        return f"https://wa.me/{recipient.lstrip('+')}?text={quote(body)}"


class InstagramAssisted(AssistedChannel):
    channel = Channel.INSTAGRAM

    def chat_link(self, recipient: str, body: str) -> str:
        return f"https://ig.me/m/{handle_from_url(recipient)}"


class FacebookAssisted(AssistedChannel):
    channel = Channel.FACEBOOK

    def chat_link(self, recipient: str, body: str) -> str:
        handle = handle_from_url(recipient)
        # Páginas sin nombre de usuario (profile.php?id=...) no tienen enlace m.me: se abre la página.
        return recipient if handle in ("", "profile.php", "people", "pages") else f"https://m.me/{handle}"


class LinkedInAssisted(AssistedChannel):
    channel = Channel.LINKEDIN

    def chat_link(self, recipient: str, body: str) -> str:
        return recipient  # el perfil o la página; el mensaje se pega a mano


ASSISTED_CHANNELS: dict[Channel, AssistedChannel] = {
    c.channel: c for c in (WhatsAppAssisted(), InstagramAssisted(), FacebookAssisted(), LinkedInAssisted())
}
