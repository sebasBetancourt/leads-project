"""Visita la web del negocio (home + páginas de contacto) y devuelve el HTML. Basado en fujiwarachoki/leadgen."""

from dataclasses import dataclass, field

# Páginas donde suele estar el contacto en sitios colombianos.
CONTACT_PATHS = ["/", "/contacto", "/contactanos", "/contact", "/nosotros", "/about"]


@dataclass
class CrawledSite:
    url: str
    pages: dict[str, str] = field(default_factory=dict)  # url -> html
    error: str | None = None


def crawl_site(url: str, *, max_pages: int = 4, timeout: float = 10.0) -> CrawledSite:
    """Sin JavaScript (httpx). Respeta robots.txt y un user-agent identificable."""
    raise NotImplementedError("F1")
