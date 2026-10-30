from datetime import date

from leadbot.enrichment.signals import detect_signals, find_shop_link

SHOPIFY = """<html><head><script src="https://cdn.shopify.com/s/files/theme.js"></script></head>
<body><a href="/cart">Carrito</a><p>Casco integral $ 245.900</p><img src="wompi-logo.png">
<footer>© 2025 Motos BGA</footer></body></html>"""

OLD_SITE = """<html><head><link href="/wp-content/themes/x/style.css"></head><body>
<a href="https://wa.me/573001234567">Pide por WhatsApp</a>
<a href="/files/catalogo-2019.pdf">Descarga nuestro catálogo</a>
<a href="https://articulo.mercadolibre.com.co/MCO-1">Mercado Libre</a>
<p>Copyright 2016 - 2019 Ferretería El Tornillo. Pagos con PSE.</p></body></html>"""


def test_detects_store_platform_cart_prices_and_gateway():
    signals = detect_signals({"https://motosbga.com/": SHOPIFY})
    assert signals.store_platforms == ["shopify"]
    assert signals.has_cart and signals.shows_prices
    assert signals.payment_gateways == ["wompi"]
    assert signals.copyright_year == 2025
    assert not signals.outdated(date(2026, 10, 30))


def test_detects_old_site_selling_by_whatsapp_and_pdf():
    signals = detect_signals({"https://eltornillo.co/": OLD_SITE})
    assert signals.store_platforms == []
    assert signals.site_builders == ["wordpress"]
    assert signals.whatsapp_links
    assert signals.pdf_catalogs == ["https://eltornillo.co/files/catalogo-2019.pdf"]
    assert signals.marketplaces == ["mercadolibre"]
    assert signals.payment_gateways == ["pse"]
    assert signals.copyright_year == 2019 and signals.outdated(date(2026, 10, 30))
    assert not signals.has_cart


def test_pse_needs_a_whole_word():
    assert detect_signals({"https://x.co/": "<p>Eclipse y pseudónimos</p>"}).payment_gateways == []


def test_flags_pages_built_with_javascript():
    html = "<html><body><div id='root'></div>" + "<script>var x = 1;</script>" * 400 + "</body></html>"
    assert detect_signals({"https://x.co/": html}).js_rendered


def test_summary_only_states_facts():
    summary = detect_signals({"https://eltornillo.co/": OLD_SITE}).summary(date(2026, 10, 30))
    assert "Carrito de compras: no detectado" in summary
    assert "posiblemente desactualizada" in summary


def test_find_shop_link_stays_on_the_same_site():
    html = """<a href="https://instagram.com/tienda">IG</a><a href="/">Inicio</a>
    <a href="/productos/">Nuestros productos</a>"""
    assert find_shop_link(html, "https://motosbga.com/") == "https://motosbga.com/productos/"
    assert find_shop_link("<a href='/contacto'>Contacto</a>", "https://motosbga.com/") is None
