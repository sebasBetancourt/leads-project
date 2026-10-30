"""Señales de presencia digital sacadas del HTML, sin LLM: plataforma, pasarela, carrito, catálogo en PDF...

El agente las recibe como hechos ("tiene carrito Shopify") para no tener que adivinarlos del texto,
lo que baja las alucinaciones y el costo. Funcionan aunque la tienda cargue con JavaScript: las
huellas (scripts de cdn.shopify.com, wixstatic...) están en el HTML aunque el texto no.
"""

import re
from datetime import date
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel
from selectolax.lexbor import LexborHTMLParser as HTMLParser

# Plataformas de tienda en línea: si aparece una, el negocio ya vende en línea (con un tercero).
STORE_PLATFORMS: dict[str, tuple[str, ...]] = {
    "shopify": ("cdn.shopify.com", "shopify.theme", "myshopify.com"),
    "woocommerce": ("woocommerce", "wc-ajax", "wc-block"),
    "vtex": ("vtex.com", "vteximg", "vtexassets"),
    "tiendanube": ("tiendanube", "nuvemshop"),
    "jumpseller": ("jumpseller",),
    "prestashop": ("prestashop",),
    "magento": ("mage/cookies", "magento"),
    "wix_stores": ("wix-stores", "wixstores", "ecom.wixapps"),
}
# Constructores de sitios: hay web, pero no necesariamente tienda.
SITE_BUILDERS: dict[str, tuple[str, ...]] = {
    "wix": ("wixstatic.com", "_wixcssimports", "wix.com website builder"),
    "wordpress": ("wp-content", "wp-includes"),
    "squarespace": ("squarespace",),
    "godaddy": ("img1.wsimg.com", "godaddy website builder"),
    "google_sites": ("sites.google.com",),
}
# Pasarelas usadas en Colombia.
PAYMENT_GATEWAYS: dict[str, tuple[str, ...]] = {
    "wompi": ("wompi",),
    "payu": ("payu",),
    "epayco": ("epayco",),
    "mercadopago": ("mercadopago", "mercado pago"),
    "bold": ("checkout.bold.co", "bold.co/"),
    "addi": ("addi.com", "co.addi"),
    "sistecredito": ("sistecredito",),
    "paypal": ("paypal.com",),
    "stripe": ("js.stripe.com",),
}
PSE_RE = re.compile(r"\bpse\b")
MARKETPLACES: dict[str, tuple[str, ...]] = {
    "mercadolibre": ("mercadolibre.com", "mercadolibre.co"),
    "rappi": ("rappi.com",),
    "falabella": ("falabella.com",),
    "linio": ("linio.com",),
    "amazon": ("amazon.com",),
    "didi_food": ("didi-food.com", "didifood"),
}
CART_RE = re.compile(
    r"add[-_]to[-_]cart|agregar al carrito|añadir al carrito|anadir al carrito|/cart\b|/carrito|/checkout|comprar ahora"
)
PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:[.,]\d{3})+")  # $ 45.900 / $1,250,000
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.IGNORECASE)
WHATSAPP_HOSTS = ("wa.me", "api.whatsapp.com", "wa.link")
SHOP_WORDS = ("tienda", "shop", "store", "productos", "catalogo", "catálogo", "comprar", "collections")
JS_ONLY_TEXT_CHARS = 300  # menos texto que esto en un HTML grande: la página se arma con JavaScript


class SiteSignals(BaseModel):
    store_platforms: list[str] = []
    site_builders: list[str] = []
    payment_gateways: list[str] = []
    marketplaces: list[str] = []
    has_cart: bool = False
    shows_prices: bool = False
    pdf_catalogs: list[str] = []
    whatsapp_links: bool = False
    copyright_year: int | None = None
    js_rendered: bool = False  # el texto puede estar incompleto

    def outdated(self, today: date | None = None) -> bool:
        year = (today or date.today()).year
        return self.copyright_year is not None and self.copyright_year < year - 2

    def summary(self, today: date | None = None) -> str:
        """Texto corto para el prompt: solo hechos detectados."""
        lines = []
        if self.store_platforms:
            lines.append(f"Plataforma de tienda detectada: {', '.join(self.store_platforms)}")
        if self.site_builders:
            lines.append(f"Sitio hecho con: {', '.join(self.site_builders)}")
        lines.append(f"Carrito de compras: {'sí' if self.has_cart else 'no detectado'}")
        lines.append(
            f"Pasarela de pagos: {', '.join(self.payment_gateways) if self.payment_gateways else 'no detectada'}"
        )
        if self.shows_prices:
            lines.append("Muestra precios en la web")
        if self.pdf_catalogs:
            lines.append(f"Catálogos en PDF: {len(self.pdf_catalogs)}")
        if self.marketplaces:
            lines.append(f"Enlaces a marketplaces: {', '.join(self.marketplaces)}")
        if self.whatsapp_links:
            lines.append("Tiene botones o enlaces a WhatsApp")
        if self.copyright_year:
            note = " (posiblemente desactualizada)" if self.outdated(today) else ""
            lines.append(f"Año del copyright: {self.copyright_year}{note}")
        if self.js_rendered:
            lines.append("La web se arma con JavaScript: el texto leído puede estar incompleto")
        return "\n".join(f"- {line}" for line in lines)


def _matches(haystack: str, catalog: dict[str, tuple[str, ...]]) -> list[str]:
    return [name for name, needles in catalog.items() if any(n in haystack for n in needles)]


def detect_signals(pages: dict[str, str]) -> SiteSignals:
    """pages: url -> html (lo que devuelve enrichment.crawler o tools.web_reader)."""
    signals = SiteSignals()
    store, builders, gateways, markets, pdfs = set(), set(), set(), set(), set()
    years: list[int] = []

    for url, html in pages.items():
        lower = html.lower()
        store.update(_matches(lower, STORE_PLATFORMS))
        builders.update(_matches(lower, SITE_BUILDERS))
        gateways.update(_matches(lower, PAYMENT_GATEWAYS))
        if PSE_RE.search(lower):
            gateways.add("pse")
        signals.has_cart |= bool(CART_RE.search(lower))
        signals.shows_prices |= bool(PRICE_RE.search(html))
        years += [int(y) for y in COPYRIGHT_RE.findall(html) if 1995 <= int(y) <= 2100]

        tree = HTMLParser(html)
        for node in tree.css("a[href]"):
            href = urljoin(url, (node.attributes.get("href") or "").strip())
            host = urlparse(href).netloc.lower()
            if urlparse(href).path.lower().endswith(".pdf"):
                pdfs.add(href)
            if any(host.endswith(h) for h in WHATSAPP_HOSTS):
                signals.whatsapp_links = True
            markets.update(name for name, hosts in MARKETPLACES.items() if any(h in host for h in hosts))

        tree.strip_tags(["script", "style", "noscript", "template"])
        text = tree.body.text(separator=" ", strip=True) if tree.body else ""
        if len(html) > 5000 and len(text) < JS_ONLY_TEXT_CHARS:
            signals.js_rendered = True

    # Una tienda Wix se detecta como wix_stores; "wix" como constructor sigue siendo cierto.
    signals.store_platforms = sorted(store)
    signals.site_builders = sorted(builders)
    signals.payment_gateways = sorted(gateways)
    signals.marketplaces = sorted(markets)
    signals.pdf_catalogs = sorted(pdfs)[:10]
    signals.copyright_year = max(years) if years else None
    return signals


def find_shop_link(html: str, base_url: str) -> str | None:
    """Primer enlace del mismo sitio que parece la tienda o el catálogo."""
    host = urlparse(base_url).netloc
    for node in HTMLParser(html).css("a[href]"):
        url = urljoin(base_url, node.attributes.get("href") or "").split("#")[0]
        label = f"{url} {node.text(strip=True)}".lower()
        if (
            urlparse(url).netloc == host
            and url.rstrip("/") != base_url.rstrip("/")
            and any(w in label for w in SHOP_WORDS)
        ):
            return url
    return None
