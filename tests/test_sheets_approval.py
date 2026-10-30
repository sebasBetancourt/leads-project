"""Pestaña Aprobación: lectura de filas y armado de la grilla, sin la API de Google."""

from leadbot.leads.approval import ReviewRow
from leadbot.leads.models import Company, ContactChannel, Message, Research
from leadbot.leads.status import Channel, MessageStatus
from leadbot.sheets.sync import APPROVAL_COLUMNS, approval_row, plan_approval_grid, read_review_rows


def message(id_: int, channel=Channel.WHATSAPP, body="Hola Motos BGA", flags=()) -> Message:
    company = Company(id=10, name="Motos BGA", city="Bucaramanga", score=82, raw={})
    company.research = [Research(id=1, case_study="tiendah2r")]
    contact = ContactChannel(channel=channel, value="+573001234567" if channel == Channel.WHATSAPP else "a@b.co")
    return Message(
        id=id_,
        company=company,
        contact=contact,
        channel=channel,
        body=body,
        review_flags=list(flags),
        subject=None if channel == Channel.WHATSAPP else "Asunto",
        status=MessageStatus.PENDING_APPROVAL,
    )


def sheet(*rows: list) -> list[list[str]]:
    return [APPROVAL_COLUMNS, *[[str(c) for c in r] for r in rows]]


def test_approval_row_has_chat_link_and_alerts():
    row = dict(
        zip(APPROVAL_COLUMNS, approval_row(message(1, flags=["muy largo"]), "decisión desconocida"), strict=True)
    )
    assert row["abrir chat"] == "https://wa.me/573001234567?text=Hola%20Motos%20BGA"
    assert row["caso"] == "tiendah2r" and row["score"] == 82
    assert row["alertas"] == "muy largo; decisión desconocida"
    assert row["decisión"] == "" and row["mensaje"] == "Hola Motos BGA"


def test_email_has_no_chat_link():
    row = dict(zip(APPROVAL_COLUMNS, approval_row(message(1, Channel.EMAIL)), strict=True))
    assert row["abrir chat"] == "" and row["asunto"] == "Asunto"


def test_read_review_rows_by_column_name_and_skips_junk():
    values = sheet(
        approval_row(message(1))[:9] + ["", "Mi versión", "aprobado", "ok"],
        ["", "fila vacía"],
        ["no-es-id"],
    )
    assert read_review_rows(values) == [ReviewRow(1, "", "Mi versión", "aprobado", "ok")]
    assert read_review_rows([]) == []


def test_grid_keeps_your_edits_drops_decided_rows_and_appends_new_ones():
    current = [
        ReviewRow(1, "", "Editado por mí", "", "pendiente de revisar"),  # sigue pendiente
        ReviewRow(2, "", "Ya aprobado", "APROBADO", ""),  # ya se procesó: sale
    ]
    pending = [message(1, body="Editado por mí"), message(3, body="Nuevo")]
    grid = plan_approval_grid(current, pending, invalid={1: "REHACER necesita notas"})
    assert [r[0] for r in grid] == [1, 3]
    first = dict(zip(APPROVAL_COLUMNS, grid[0], strict=True))
    assert first["mensaje"] == "Editado por mí" and first["notas"] == "pendiente de revisar"
    assert first["alertas"] == "REHACER necesita notas"
    assert "Editado%20por%20m%C3%AD" in first["abrir chat"]  # el enlace usa el texto que aprobarás


def test_duplicate_rows_are_written_once():
    grid = plan_approval_grid([ReviewRow(1, "", "a", "", ""), ReviewRow(1, "", "b", "", "")], [message(1)])
    assert len(grid) == 1
