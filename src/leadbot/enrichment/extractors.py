"""Extrae canales de contacto del HTML: correos, Instagram, Facebook, LinkedIn y WhatsApp (wa.me)."""

from dataclasses import dataclass, field


@dataclass
class ExtractedContacts:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)  # E.164
    instagram: set[str] = field(default_factory=set)
    facebook: set[str] = field(default_factory=set)
    linkedin: set[str] = field(default_factory=set)
    whatsapp: set[str] = field(default_factory=set)  # E.164 desde enlaces wa.me / api.whatsapp.com


def extract_contacts(html: str, base_url: str) -> ExtractedContacts:
    """Descarta correos de plantillas (ejemplo@, sentry, wixpress) y enlaces de compartir."""
    raise NotImplementedError("F1")
