"""Importar la exportación de la pestaña Leads (necesita TEST_DATABASE_URL)."""

import openpyxl
from sqlalchemy import select

from leadbot.leads.models import Company, ContactChannel, Event
from leadbot.leads.status import Channel, LeadStatus
from leadbot.sheets.importer import import_xlsx, place_id_from_link
from leadbot.sheets.sync import LEADS_COLUMNS

LINK = (
    "https://www.google.com/maps/place/Bulevar+Motos/data=!4m7!3m6!1s0x8e6:0xdaf!19sChIJaf6bdHAVaI4RfkGyc3QL99o?hl=es"
)


def write_xlsx(path, rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Leads"
    sheet.append(LEADS_COLUMNS)
    for row in rows:
        sheet.append([row.get(c, "") for c in LEADS_COLUMNS])
    workbook.save(path)
    return path


ROWS = [
    {
        "id": 1,
        "empresa": "Bulevar Motos",
        "sector": "repuestos_autopartes",
        "ciudad": "Bucaramanga",
        "categoría": "Tienda de repuestos para motos",
        "rating": 4.0,
        "reseñas": 5,
        "estado": "ENRICHED",
        "teléfono": "+573155555410",
        "whatsapp": "+573155555410",
        "email": "a@bulevar.co\nb@bulevar.co",
        "google maps": LINK,
    },
    {
        "id": 2,
        "empresa": "Disanmotos",
        "sector": "repuestos_autopartes",
        "ciudad": "Bucaramanga",
        "estado": "UNQUALIFIED",
        "teléfono": "607 634 0005",
    },
    {"id": 3, "empresa": "", "sector": "", "ciudad": ""},
]


def test_place_id_comes_from_the_maps_link():
    assert place_id_from_link(LINK) == "ChIJaf6bdHAVaI4RfkGyc3QL99o"
    assert place_id_from_link("https://maps.google.com/?q=x") is None


def test_imports_leads_with_status_and_contacts(db, tmp_path):
    stats = import_xlsx(write_xlsx(tmp_path / "leads.xlsx", ROWS))
    assert (stats.rows, stats.created, stats.skipped, stats.contacts) == (3, 2, 1, 3)

    bulevar = db.scalar(select(Company).where(Company.name == "Bulevar Motos"))
    assert bulevar.status == LeadStatus.ENRICHED and bulevar.place_id == "ChIJaf6bdHAVaI4RfkGyc3QL99o"
    assert (bulevar.rating, bulevar.reviews_count, bulevar.raw) == (4.0, 5, {"link": LINK})
    contacts = db.scalars(select(ContactChannel).where(ContactChannel.company_id == bulevar.id)).all()
    assert sorted((c.channel, c.value) for c in contacts) == [
        (Channel.EMAIL, "a@bulevar.co"),
        (Channel.EMAIL, "b@bulevar.co"),
        (Channel.WHATSAPP, "+573155555410"),
    ]
    disan = db.scalar(select(Company).where(Company.name == "Disanmotos"))
    assert disan.status == LeadStatus.UNQUALIFIED and disan.phone_e164 == "+576076340005"
    assert db.query(Event).filter(Event.kind == "imported").count() == 2


def test_importing_twice_does_not_duplicate(db, tmp_path):
    path = write_xlsx(tmp_path / "leads.xlsx", ROWS)
    import_xlsx(path)
    stats = import_xlsx(path)
    assert (stats.created, stats.existing) == (0, 2)
    assert db.query(Company).count() == 2
