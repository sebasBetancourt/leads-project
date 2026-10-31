"""Fixtures compartidas.

Las pruebas que tocan Postgres usan la fixture `db` y se saltan si no hay TEST_DATABASE_URL:

    docker run -d --name leadbot-test-pg -e POSTGRES_USER=leadbot -e POSTGRES_DB=leadbot_test \
        -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql+psycopg://leadbot@127.0.0.1:55432/leadbot_test uv run pytest

La base es desechable y no tiene contraseña (solo escucha en 127.0.0.1): no uses estos valores
para la base de trabajo. En CI la provee el servicio postgres del workflow.
"""

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    # Antes de importar leadbot: core/db.py crea el engine al importarse.
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402

LEAKY_ENV = (
    "GOOGLE_SHEET_ID",
    "GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "SMTP_HOST",
    "IMAP_HOST",
    "LOG_FORMAT",
    *(f"PAUSE_{name}" for name in ("ALL", "EMAIL", "WHATSAPP", "INSTAGRAM", "FACEBOOK", "LINKEDIN")),
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """litellm hace load_dotenv() al importarse y mete tu .env real en os.environ: las pruebas no lo ven."""
    for name in LEAKY_ENV:
        monkeypatch.delenv(name, raising=False)


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
                "TRUNCATE companies, contact_channels, research, messages, events, suppressions, jobs, "
                "runtime_flags, mail_cursor, worker_heartbeats "
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


@pytest.fixture
def make_message(db):
    """Crea un mensaje de email de un lead: make_message(lead, status=..., step=..., sent_at=...)."""
    from sqlalchemy import select

    from leadbot.leads.models import ContactChannel, Message
    from leadbot.leads.status import Channel, MessageStatus

    def _make(lead, *, status=MessageStatus.APPROVED, step=0, subject="Su tienda en línea", body="Hola.", **fields):
        contact = db.scalar(
            select(ContactChannel).where(ContactChannel.company_id == lead.id, ContactChannel.channel == Channel.EMAIL)
        )
        message = Message(
            company_id=lead.id,
            contact_id=contact.id,
            channel=Channel.EMAIL,
            step=step,
            subject=subject,
            body=body,
            status=status,
            **fields,
        )
        db.add(message)
        db.commit()
        return message

    return _make
