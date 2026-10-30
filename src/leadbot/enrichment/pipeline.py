"""Etapa 2: leads NEW -> canales de contacto desde Google Maps y su web -> ENRICHED (o UNQUALIFIED)."""

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from leadbot.core.db import session_scope
from leadbot.enrichment.crawler import crawl_site
from leadbot.enrichment.extractors import (
    ExtractedContacts,
    clean_email,
    extract_contacts,
    keep_relevant,
    social_profile,
)
from leadbot.enrichment.phone import is_mobile
from leadbot.leads.models import Company
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import Channel, LeadStatus

log = logging.getLogger(__name__)

# "Webs" que en realidad son una red social o un agregador de enlaces.
SOCIAL_HOSTS = ("instagram.com", "facebook.com", "fb.com", "wa.me", "whatsapp.com", "linkedin.com")
# Directorios y marcas que los negocios ponen como "su web" en Google Maps: no son suyas,
# así que no se rastrean y sus correos se descartan (serían de la marca o del directorio).
NOT_OWN_SITE_HOSTS = (
    "paginasamarillas.com.co", "cylex.com.co", "empresite.com.co", "guiaempresas.com", "auteco.com.co",
    "yamaha-motor.com.co", "hondamotos.com.co", "sites.google.com",
)


@dataclass
class EnrichmentStats:
    processed: int = 0
    enriched: int = 0
    unqualified: int = 0


def collect_contacts(company: Company) -> tuple[ExtractedContacts, str | None]:
    """Junta lo que dio Google Maps con lo que hay en su web. Devuelve (contactos, error de la web)."""
    contacts = ExtractedContacts()
    host = urlparse(company.website or "").netloc.lower()
    own_site = not any(host.endswith(h) for h in NOT_OWN_SITE_HOSTS)
    if company.phone_e164:
        contacts.phones.add(company.phone_e164)
    if own_site:
        # gosom saca estos correos de la misma web: pasan por el mismo filtro que los nuestros.
        from_gmaps = ExtractedContacts(
            emails={e for raw in (company.raw or {}).get("emails", "").split(",") if (e := clean_email(raw))}
        )
        contacts.merge(keep_relevant(from_gmaps, name=company.name, website=company.website))

    error = None
    if company.website and own_site:
        if any(host.endswith(h) for h in SOCIAL_HOSTS):
            if profile := social_profile(company.website):
                getattr(contacts, profile[0]).add(profile[1])
        else:
            site = crawl_site(company.website)
            error = site.error
            from_site = ExtractedContacts()
            for url, html in site.pages.items():
                from_site.merge(extract_contacts(html, url))
            contacts.merge(keep_relevant(from_site, name=company.name, website=company.website))

    # Solo los celulares pueden tener WhatsApp.
    contacts.whatsapp.update(p for p in contacts.phones if is_mobile(p))
    return contacts, error


def save_contacts(repo: LeadRepository, company: Company, contacts: ExtractedContacts) -> int:
    from_gmaps = {company.phone_e164} | {e.strip().lower() for e in (company.raw or {}).get("emails", "").split(",")}
    added = 0
    for channel, values in (
        (Channel.EMAIL, contacts.emails),
        (Channel.WHATSAPP, contacts.whatsapp),
        (Channel.INSTAGRAM, contacts.instagram),
        (Channel.FACEBOOK, contacts.facebook),
        (Channel.LINKEDIN, contacts.linkedin),
    ):
        for value in sorted(values):
            added += repo.add_contact(company, channel, value, "gmaps" if value in from_gmaps else "website")
    return added


def run_enrichment(limit: int = 50) -> EnrichmentStats:
    stats = EnrichmentStats()
    with session_scope() as session:
        repo = LeadRepository(session)
        companies = repo.fetch_by_status(LeadStatus.NEW, limit=limit)

    for company in companies:
        contacts, error = collect_contacts(company)
        with session_scope() as session:
            repo = LeadRepository(session)
            company = session.merge(company)
            save_contacts(repo, company, contacts)
            session.flush()
            session.refresh(company, ["contacts"])
            if company.contacts:
                repo.change_status(company, LeadStatus.ENRICHED, error or "")
                stats.enriched += 1
            else:
                repo.change_status(company, LeadStatus.UNQUALIFIED, "sin canal de contacto")
                stats.unqualified += 1
        stats.processed += 1
        log.info("%-40s %d canales", company.name[:40], len(company.contacts))
    return stats
