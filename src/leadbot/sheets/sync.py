"""Google Sheets como interfaz del MVP. Postgres es la fuente de verdad; Sheets es la vista.

Pestañas:
    Leads      solo lectura: empresa, sector, ciudad, score, estado, canales.
    Aprobación editable: mensaje (puedes reescribirlo), enlace para abrir el chat (redes, modo asistido),
               columna Decisión (APROBADO / REHACER / DESCARTAR) y Notas. Se lee de vuelta a Postgres.
    Respuestas editable: marcas a mano las respuestas de redes (REPLIED / MEETING / WON / LOST / OPT_OUT). (F4)
    Métricas   solo lectura: enviados, respuestas y reuniones por canal, sector y ciudad. (F5)

Regla de conflicto: Sheets solo puede cambiar las columnas editables. Todo lo demás se sobrescribe
desde Postgres en cada sincronización.

Orden de cada sincronización (`sync`): primero se leen tus decisiones y ediciones (pull) y después
se reescribe la pestaña (push) conservando lo editable que acaba de leerse. Así no se pierde una
edición que todavía no había llegado a Postgres. Las filas ya decididas salen de la pestaña.
"""

from dataclasses import dataclass
from pathlib import Path

import gspread
from gspread.utils import ValidationConditionType

from leadbot.core.db import session_scope
from leadbot.leads.approval import Decision, ReviewRow, ReviewStats, apply_review
from leadbot.leads.models import Company, Message
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import Channel
from leadbot.outreach.channels.assisted import ASSISTED_CHANNELS

LEADS_TAB = "Leads"
APPROVAL_TAB = "Aprobación"
REPLIES_TAB = "Respuestas"
METRICS_TAB = "Métricas"

LEADS_COLUMNS = [
    "id",
    "empresa",
    "sector",
    "ciudad",
    "categoría",
    "rating",
    "reseñas",
    "estado",
    "score",
    "caso",
    "resumen",
    "web",
    "teléfono",
    "email",
    "whatsapp",
    "instagram",
    "facebook",
    "linkedin",
    "google maps",
]

# Las columnas fijas van primero y las editables al final: así cada bloque es un rango contiguo.
APPROVAL_COLUMNS = [
    "message_id",
    "empresa",
    "ciudad",
    "canal",
    "destinatario",
    "abrir chat",
    "score",
    "caso",
    "alertas",
    "asunto",
    "mensaje",
    "decisión",
    "notas",
]
EDITABLE_APPROVAL_COLUMNS = {"asunto", "mensaje", "decisión", "notas"}
FIXED_APPROVAL_COUNT = len(APPROVAL_COLUMNS) - len(EDITABLE_APPROVAL_COLUMNS)
MAX_ROWS = 1000


def _latest(company: Company):
    return max(company.research, key=lambda r: r.id) if company.research else None


def lead_row(company: Company) -> list:
    by_channel: dict[Channel, list[str]] = {}
    for contact in company.contacts:
        by_channel.setdefault(contact.channel, []).append(contact.value)

    def joined(channel: Channel) -> str:
        return "\n".join(by_channel.get(channel, []))

    research = _latest(company)
    return [
        company.id,
        company.name,
        company.sector,
        company.city,
        company.google_category or "",
        company.rating or "",
        company.reviews_count or "",
        company.status.value,
        company.score or "",
        (research.case_study or "") if research else "",
        research.summary if research else "",
        company.website or "",
        company.phone_e164 or "",
        joined(Channel.EMAIL),
        joined(Channel.WHATSAPP),
        joined(Channel.INSTAGRAM),
        joined(Channel.FACEBOOK),
        joined(Channel.LINKEDIN),
        (company.raw or {}).get("link", ""),
    ]


def chat_link(message: Message) -> str:
    assisted = ASSISTED_CHANNELS.get(message.channel)
    return assisted.chat_link(message.contact.value, message.body) if assisted else ""


def approval_row(message: Message, alert: str = "") -> list:
    """Fila completa de un mensaje en PENDING_APPROVAL (fijas + editables con el valor de Postgres)."""
    research = _latest(message.company)
    alerts = "; ".join([*(message.review_flags or []), *([alert] if alert else [])])
    return [
        message.id,
        message.company.name,
        message.company.city,
        message.channel.value,
        message.contact.value,
        chat_link(message),
        message.company.score or "",
        (research.case_study or "") if research else "",
        alerts,
        message.subject or "",
        message.body,
        "",
        "",
    ]


def _cell(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def read_review_rows(values: list[list[str]]) -> list[ReviewRow]:
    """Filas de la pestaña Aprobación tal como están en Sheets (se ubican las columnas por su nombre)."""
    if not values:
        return []
    header = values[0]
    col = {name: header.index(name) for name in APPROVAL_COLUMNS if name in header}
    if "message_id" not in col:
        return []
    rows = []
    for raw in values[1:]:
        message_id = _cell(raw, col["message_id"]).strip()
        if not message_id.isdigit():
            continue
        rows.append(
            ReviewRow(
                message_id=int(message_id),
                subject=_cell(raw, col.get("asunto", 10**6)),
                body=_cell(raw, col.get("mensaje", 10**6)),
                decision=_cell(raw, col.get("decisión", 10**6)),
                notes=_cell(raw, col.get("notas", 10**6)),
            )
        )
    return rows


def plan_approval_grid(
    current: list[ReviewRow], pending: list[Message], invalid: dict[int, str] | None = None
) -> list[list]:
    """Contenido nuevo de la pestaña (sin encabezado).

    - Mensajes que siguen pendientes y ya estaban: columnas fijas desde Postgres, editables como las dejaste.
    - Mensajes nuevos: al final.
    - Mensajes ya decididos: desaparecen.
    """
    invalid = invalid or {}
    by_id = {m.id: m for m in pending}
    grid, seen = [], set()
    for row in current:
        message = by_id.get(row.message_id)
        if message is None or row.message_id in seen:
            continue
        seen.add(row.message_id)
        fixed = approval_row(message, invalid.get(message.id, ""))[:FIXED_APPROVAL_COUNT]
        grid.append([*fixed, row.subject, row.body, row.decision, row.notes])
    grid += [approval_row(m) for m in pending if m.id not in seen]
    return grid


@dataclass
class SyncStats:
    leads: int = 0
    pending: int = 0
    review: ReviewStats | None = None


class SheetsSync:
    def __init__(self, service_account_file: str, sheet_id: str):
        if not sheet_id:
            raise RuntimeError("Falta GOOGLE_SHEET_ID en .env")
        if not Path(service_account_file).exists():
            raise RuntimeError(f"No existe {service_account_file}: descarga el JSON de la cuenta de servicio")
        self.sheet = gspread.service_account(filename=service_account_file).open_by_key(sheet_id)

    def _tab(self, title: str, columns: int) -> gspread.Worksheet:
        try:
            return self.sheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.sheet.add_worksheet(title=title, rows=MAX_ROWS, cols=columns)

    def push(self) -> int:
        """Postgres -> Sheets: pestaña Leads completa. Devuelve cuántas filas escribió."""
        with session_scope() as session:
            rows = [lead_row(c) for c in LeadRepository(session).all_with_contacts()]

        tab = self._tab(LEADS_TAB, len(LEADS_COLUMNS))
        if tab.index != 0:
            tab.update_index(0)  # que sea lo primero que ves al abrir la hoja
        tab.clear()
        tab.update([LEADS_COLUMNS, *rows], "A1")
        tab.freeze(rows=1)
        return len(rows)

    def pull_decisions(self) -> ReviewStats:
        """Sheets -> Postgres: aprobaciones y ediciones de la pestaña Aprobación.

        Marcar ENVIADO (redes, F4) y la pestaña Respuestas se leen aquí cuando lleguen esas fases.
        """
        tab = self._tab(APPROVAL_TAB, len(APPROVAL_COLUMNS))
        rows = read_review_rows(tab.get_all_values())
        with session_scope() as session:
            return apply_review(session, rows)

    def push_approval(self, invalid: dict[int, str] | None = None) -> int:
        """Postgres -> pestaña Aprobación, conservando lo editable que tenga cada fila."""
        tab = self._tab(APPROVAL_TAB, len(APPROVAL_COLUMNS))
        values = tab.get_all_values()
        first_time = not values or values[0][: len(APPROVAL_COLUMNS)] != APPROVAL_COLUMNS
        with session_scope() as session:
            grid = plan_approval_grid(
                read_review_rows(values), LeadRepository(session).pending_approval_messages(), invalid
            )

        old_rows = max(len(values) - 1, 0)
        tab.update([APPROVAL_COLUMNS, *grid], "A1", raw=True)
        if old_rows > len(grid):
            tab.batch_clear([f"A{len(grid) + 2}:M{old_rows + 1}"])
        if first_time:
            self._format_approval(tab)
        return len(grid)

    def _format_approval(self, tab: gspread.Worksheet) -> None:
        decision_col = chr(ord("A") + APPROVAL_COLUMNS.index("decisión"))
        message_col = chr(ord("A") + APPROVAL_COLUMNS.index("mensaje"))
        tab.freeze(rows=1)
        tab.add_validation(
            f"{decision_col}2:{decision_col}{MAX_ROWS}",
            ValidationConditionType.one_of_list,
            [d.value for d in Decision],
            showCustomUi=True,
            inputMessage="APROBADO, REHACER (con notas) o DESCARTAR",
        )
        tab.format(f"{message_col}2:{message_col}{MAX_ROWS}", {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"})
        tab.format("A1:M1", {"textFormat": {"bold": True}})

    def sync(self) -> SyncStats:
        """Lo que corre el worker cada 5 minutos: primero tus decisiones, después la vista."""
        review = self.pull_decisions()
        pending = self.push_approval(review.invalid)
        leads = self.push()
        return SyncStats(leads=leads, pending=pending, review=review)
