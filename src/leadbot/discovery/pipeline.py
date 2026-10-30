"""Etapa 1: búsquedas sector × ciudad en Google Maps -> companies (NEW)."""

import logging
from dataclasses import dataclass

from leadbot.core.config import get_settings
from leadbot.core.db import session_scope
from leadbot.discovery.gmaps_client import GmapsClient, GmapsPlace
from leadbot.discovery.query_builder import SearchQuery
from leadbot.enrichment.phone import normalize_co
from leadbot.leads.repository import LeadRepository

log = logging.getLogger(__name__)


@dataclass
class DiscoveryStats:
    queries: int = 0
    found: int = 0
    new: int = 0
    failed: int = 0


def company_fields(place: GmapsPlace) -> dict:
    return {
        "name": place.name,
        "address": place.address,
        "website": place.website,
        "google_category": place.category,
        "rating": place.rating,
        "reviews_count": place.reviews_count,
        "raw": place.raw,
    }


def run_discovery(queries: list[SearchQuery], *, depth: int = 1) -> DiscoveryStats:
    """Una búsqueda a la vez: gosom procesa los trabajos en serie y así cada lead sabe de qué búsqueda salió."""
    client = GmapsClient(get_settings().gmaps_scraper_url)
    stats = DiscoveryStats()

    for query in queries:
        stats.queries += 1
        log.info("Buscando: %s", query.keyword)
        job = client.wait(client.create_job(query, depth=depth).id)
        if job.status != "ok":
            log.warning("Búsqueda %r terminó en %s", query.keyword, job.status)
            stats.failed += 1
            continue

        places = client.download_results(job.id)
        with session_scope() as session:
            repo = LeadRepository(session)
            for place in places:
                _, created = repo.upsert_company(
                    place_id=place.place_id,
                    phone_e164=normalize_co(place.phone),
                    fields=company_fields(place),
                    sector=query.sector,
                    city=query.city,
                    query=query.keyword,
                )
                stats.new += created
        stats.found += len(places)
        log.info("  %d negocios (%d nuevos en total)", len(places), stats.new)

    return stats
