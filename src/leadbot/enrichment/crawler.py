"""Visita la web del negocio (home + páginas de contacto) y devuelve el HTML. Basado en fujiwarachoki/leadgen."""

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

USER_AGENT = "BrainSolLeadbot/0.1 (+https://brainsol.vercel.app)"

# Páginas donde suele estar el contacto en sitios colombianos.
CONTACT_PATHS = ["/", "/contacto", "/contactanos", "/contact", "/nosotros", "/about"]
CONTACT_WORDS = ("contact", "nosotros", "about", "quienes-somos", "ubicacion")


@dataclass
class CrawledSite:
    url: str
    pages: dict[str, str] = field(default_factory=dict)  # url -> html
    error: str | None = None


def _robots(client: httpx.Client, base: str) -> RobotFileParser:
    parser = RobotFileParser()
    try:
        response = client.get(urljoin(base, "/robots.txt"))
        parser.parse(response.text.splitlines() if response.status_code == 200 else [])
    except httpx.HTTPError:
        parser.parse([])  # sin robots.txt accesible: se permite
    return parser


def _contact_links(html: str, base_url: str) -> list[str]:
    """Enlaces del mismo sitio que parecen páginas de contacto."""
    host = urlparse(base_url).netloc
    links = []
    for node in HTMLParser(html).css("a[href]"):
        url = urljoin(base_url, node.attributes.get("href") or "").split("#")[0]
        if urlparse(url).netloc == host and any(w in url.lower() for w in CONTACT_WORDS):
            links.append(url)
    return links


def crawl_site(url: str, *, max_pages: int = 4, timeout: float = 10.0) -> CrawledSite:
    """Sin JavaScript (httpx). Respeta robots.txt y un user-agent identificable."""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    site = CrawledSite(url=url)

    # verify=False: muchas webs de pymes tienen el certificado vencido; solo leemos páginas públicas.
    with httpx.Client(
        follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}, verify=False
    ) as client:
        try:
            home = client.get(url)
            home.raise_for_status()
        except httpx.HTTPError as e:
            site.error = f"{type(e).__name__}: {e}"[:300]
            return site

        base = str(home.url)  # después de redirecciones
        robots = _robots(client, base)
        if "html" in home.headers.get("content-type", ""):
            site.pages[base] = home.text

        candidates = _contact_links(home.text, base) + [urljoin(base, p) for p in CONTACT_PATHS[1:]]
        for candidate in dict.fromkeys(candidates):
            if len(site.pages) >= max_pages:
                break
            if candidate in site.pages or not robots.can_fetch(USER_AGENT, candidate):
                continue
            try:
                page = client.get(candidate)
            except httpx.HTTPError:
                continue
            if page.status_code == 200 and "html" in page.headers.get("content-type", ""):
                site.pages[str(page.url)] = page.text

    return site
