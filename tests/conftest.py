"""Fixtures compartidas.

Las pruebas que tocan Postgres usan la fixture `db` y se saltan si no hay TEST_DATABASE_URL:

    docker run -d --name leadbot-test-pg -e POSTGRES_USER=leadbot -e POSTGRES_PASSWORD=leadbot \
        -e POSTGRES_DB=leadbot_test -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql+psycopg://leadbot:leadbot@localhost:55432/leadbot_test uv run pytest

En CI la provee el servicio postgres del workflow.
"""

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    # Antes de importar leadbot: core/db.py crea el engine al importarse.
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def _migrated():
    if not TEST_DATABASE_URL:
        pytest.skip("sin TEST_DATABASE_URL: prueba con Postgres omitida")
    from leadbot.core.migrate import upgrade

    upgrade()


@pytest.fixture
def db(_migrated):
    """Sesión sobre una base vacía. El código bajo prueba abre sus propias sesiones (session_scope)."""
    from sqlalchemy import text

    from leadbot.core.db import SessionLocal, engine

    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE companies, contact_channels, research, messages, events, suppressions, jobs "
                "RESTART IDENTITY CASCADE"
            )
        )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def make_lead(db):
    """Crea un lead con sus canales: make_lead(status, email=..., whatsapp=...)."""
    from leadbot.leads.models import Company, ContactChannel
    from leadbot.leads.status import Channel, LeadStatus

    def _make(status=LeadStatus.ENRICHED, name="Motos BGA", website="https://motosbga.com", **channels) -> Company:
        company = Company(
            name=name, sector="repuestos_autopartes", city="Bucaramanga", status=status, website=website, raw={}
        )
        db.add(company)
        db.flush()
        for channel, value in channels.items():
            db.add(ContactChannel(company_id=company.id, channel=Channel(channel), value=value, source="website"))
        db.commit()
        return company

    return _make
