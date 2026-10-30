"""Extrae canales de contacto del HTML: correos, Instagram, Facebook, LinkedIn y WhatsApp (wa.me)."""

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from leadbot.enrichment.phone import normalize_co

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Correos de plantillas, librerías y servicios, no del negocio.
JUNK_EMAIL_PARTS = ("example", "ejemplo", "sentry", "wixpress", "domain.com", "tudominio", "correo.com", "@2x")
JUNK_EMAIL_DOMAINS = {"mail.com", "email.com"}
# Correos personales/gratuitos: el negocio suele usarlos (ventas.motos@gmail.com) aunque tenga web propia.
FREE_EMAIL_DOMAINS = {"gmail.com", "hotmail.com", "outlook.com", "outlook.es", "yahoo.com", "yahoo.es", "live.com"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")

# Rutas de redes que no son el perfil del negocio (publicaciones, botones de compartir...).
INSTAGRAM_SKIP = {"p", "reel", "reels", "explore", "stories", "accounts", "share", "tv", "direct"}
FACEBOOK_SKIP = {
    "sharer", "sharer.php", "share", "plugins", "tr", "dialog", "login", "watch", "groups", "events",
    "search", "hashtag", "photo", "photo.php", "photos", "story.php", "permalink.php", "help", "policies",
    "privacy", "media", "pages",
}


@dataclass
class ExtractedContacts:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)  # E.164
    instagram: set[str] = field(default_factory=set)
    facebook: set[str] = field(default_factory=set)
    linkedin: set[str] = field(default_factory=set)
    whatsapp: set[str] = field(default_factory=set)  # E.164 desde enlaces wa.me / api.whatsapp.com

    def merge(self, other: "ExtractedContacts") -> None:
        for name in self.__dataclass_fields__:
            getattr(self, name).update(getattr(other, name))


def clean_email(raw: str) -> str | None:
    email = unquote(raw).strip().lower().strip(".")
    if not EMAIL_RE.fullmatch(email):
        return None
    if email.endswith(IMAGE_SUFFIXES) or any(part in email for part in JUNK_EMAIL_PARTS):
        return None
    if email.split("@")[1] in JUNK_EMAIL_DOMAINS:
        return None
    return email


def _tokens(text: str) -> set[str]:
    """Palabras de 4+ letras: 'Lubricantes KAOMAR' -> {'lubricantes', 'kaomar'}."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 4}


def _site_domain(website: str | None) -> str | None:
    if not website:
        return None
    host = urlparse(website if "://" in website else f"https://{website}").netloc.lower()
    return host.removeprefix("www.") or None


def keep_relevant(found: ExtractedContacts, *, name: str, website: str | None) -> ExtractedContacts:
    """Quita lo que es de la agencia que hizo la web, de la marca que distribuye o de otro negocio.

    - Correos con dominio corporativo distinto al de su web (cliente@agencia.com, info@marca.com.co).
    - Si hay varias cuentas de la misma red, se queda con las que se parecen al nombre o al dominio.
    """
    domain = _site_domain(website)
    own_words = _tokens(name) | _tokens(domain or "")

    def own_email(email: str) -> bool:
        email_domain = email.split("@")[1]
        return email_domain in FREE_EMAIL_DOMAINS or domain is None or email_domain.endswith(domain)

    def best_profiles(urls: set[str]) -> set[str]:
        if len(urls) <= 1:
            return urls
        matching = {u for u in urls if any(w in u.lower() for w in own_words)}
        return matching or urls

    return ExtractedContacts(
        emails={e for e in found.emails if own_email(e)},
        phones=found.phones,
        instagram=best_profiles(found.instagram),
        facebook=best_profiles(found.facebook),
        linkedin=best_profiles(found.linkedin),
        whatsapp=found.whatsapp,
    )


def social_profile(url: str) -> tuple[str, str] | None:
    """Clasifica un enlace de red social y lo devuelve normalizado: ('instagram', 'https://www.instagram.com/x/')."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.").removeprefix("web.")
    parts = [p for p in parsed.path.split("/") if p]

    if host in ("wa.me", "api.whatsapp.com", "whatsapp.com"):
        digits = parts[0] if host == "wa.me" and parts else parse_qs(parsed.query).get("phone", [""])[0]
        phone = normalize_co(f"+{digits.lstrip('+')}") if digits else None
        return ("whatsapp", phone) if phone else None
    if not parts:
        return None
    if host == "instagram.com" and parts[0] not in INSTAGRAM_SKIP:
        return "instagram", f"https://www.instagram.com/{parts[0]}/"
    if host in ("facebook.com", "fb.com") and parts[0] not in FACEBOOK_SKIP:
        if parts[0] == "profile.php":
            page_id = parse_qs(parsed.query).get("id", [""])[0]
            return ("facebook", f"https://www.facebook.com/profile.php?id={page_id}") if page_id else None
        return "facebook", f"https://www.facebook.com/{parts[0]}"
    if host == "linkedin.com" and parts[0] in ("company", "in") and len(parts) > 1:
        return "linkedin", f"https://www.linkedin.com/{parts[0]}/{parts[1]}/"
    return None


def extract_contacts(html: str, base_url: str) -> ExtractedContacts:
    """Descarta correos de plantillas (ejemplo@, sentry, wixpress) y enlaces de compartir."""
    found = ExtractedContacts()
    tree = HTMLParser(html)

    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        lower = href.lower()
        if lower.startswith("mailto:"):
            if email := clean_email(href[7:].split("?")[0]):
                found.emails.add(email)
        elif lower.startswith("tel:"):
            if phone := normalize_co(href[4:]):
                found.phones.add(phone)
        elif profile := social_profile(urljoin(base_url, href)):
            getattr(found, profile[0]).add(profile[1])

    text = tree.body.text(separator=" ") if tree.body else ""
    for match in EMAIL_RE.findall(text):
        if email := clean_email(match):
            found.emails.add(email)
    return found
