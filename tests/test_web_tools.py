from leadbot.enrichment.crawler import FetchedPage
from leadbot.tools import web_reader, web_search
from leadbot.tools.web_reader import WebReaderTool, html_to_text
from leadbot.tools.web_search import SearchHit, WebSearchTool, format_hits


def test_html_to_text_keeps_visible_text_only():
    html = """<html><head><title>Motos BGA</title><meta name="description" content="Repuestos en Bucaramanga">
    <style>.a{color:red}</style></head><body><script>alert(1)</script>
    <h1>Repuestos   para motos</h1>\n\n\n<p>Envíos a todo Santander</p></body></html>"""
    assert html_to_text(html) == (
        "Título: Motos BGA\nDescripción: Repuestos en Bucaramanga\nRepuestos para motos\nEnvíos a todo Santander"
    )


def test_web_reader_truncates_and_returns_raw_html(monkeypatch):
    html = "<html><body><p>" + "palabra " * 500 + "</p></body></html>"
    monkeypatch.setattr(web_reader, "fetch_page", lambda url, timeout: FetchedPage(url="https://x.co/", html=html))
    result = WebReaderTool()(url="x.co", max_chars=100)
    assert result.ok
    assert len(result.content) <= 104 and result.content.endswith("[…]")
    assert result.data == {"url": "https://x.co/", "html": html}


def test_web_reader_reports_errors_without_raising(monkeypatch):
    monkeypatch.setattr(
        web_reader, "fetch_page", lambda url, timeout: FetchedPage(url="https://x.co/", error="ConnectTimeout")
    )
    result = WebReaderTool()(url="x.co")
    assert not result.ok and "ConnectTimeout" in result.content


def test_format_hits():
    hits = [SearchHit(title="Motos BGA", url="https://motosbga.com", snippet="Repuestos")]
    assert format_hits(hits) == "1. Motos BGA — https://motosbga.com\n   Repuestos"


def test_web_search_failure_does_not_break_the_agent(monkeypatch):
    def blocked(query, n):
        raise RuntimeError("202 Ratelimit")

    monkeypatch.setattr(web_search, "search_duckduckgo", blocked)
    monkeypatch.setattr(web_search, "MIN_SECONDS_BETWEEN_CALLS", 0)
    result = WebSearchTool("duckduckgo")(query="Motos BGA")
    assert not result.ok and "Ratelimit" in result.content


def test_web_search_returns_hits(monkeypatch):
    monkeypatch.setattr(web_search, "search_duckduckgo", lambda q, n: [SearchHit(title="t", url="https://u.co")])
    monkeypatch.setattr(web_search, "MIN_SECONDS_BETWEEN_CALLS", 0)
    result = WebSearchTool("duckduckgo")(query="Motos BGA")
    assert result.ok and result.data["hits"][0]["url"] == "https://u.co"


def test_serpapi_without_key_fails_cleanly(monkeypatch):
    monkeypatch.setattr(web_search, "MIN_SECONDS_BETWEEN_CALLS", 0)
    result = WebSearchTool("serpapi", api_key="")(query="x")
    assert not result.ok and "SERPAPI_API_KEY" in result.content
