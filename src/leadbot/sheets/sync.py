"""Google Sheets como interfaz del MVP. Postgres es la fuente de verdad; Sheets es la vista.

Pestañas:
    Leads      solo lectura: empresa, sector, ciudad, score, estado, canales.
    Aprobación editable: mensaje (puedes reescribirlo), enlace para abrir el chat (redes, modo asistido),
               columna Decisión (APROBADO / RECHAZADO / ENVIADO) y Notas. Se lee de vuelta a Postgres.
    Respuestas editable: marcas a mano las respuestas de redes (REPLIED / MEETING / WON / LOST / OPT_OUT).
    Métricas   solo lectura: enviados, respuestas y reuniones por canal, sector y ciudad.

Regla de conflicto: Sheets solo puede cambiar las columnas editables. Todo lo demás se sobrescribe
desde Postgres en cada sincronización.
"""

from pathlib import Path

import gspread

from leadbot.core.db import session_scope
from leadbot.leads.models import Company
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import Channel

LEADS_TAB = "Leads"
APPROVAL_TAB = "Aprobación"
REPLIES_TAB = "Respuestas"
METRICS_TAB = "Métricas"

LEADS_COLUMNS = [
    "id", "empresa", "sector", "ciudad", "categoría", "rating", "reseñas", "estado", "score",
    "web", "teléfono", "email", "whatsapp", "instagram", "facebook", "linkedin", "google maps",
]

APPROVAL_COLUMNS = [
    "message_id", "empresa", "ciudad", "canal", "destinatario", "abrir chat", "score", "caso",
    "asunto", "mensaje", "decisión", "notas",
]
EDITABLE_APPROVAL_COLUMNS = {"asunto", "mensaje", "decisión", "notas"}


def lead_row(company: Company) -> list:
    by_channel: dict[Channel, list[str]] = {}
    for contact in company.contacts:
        by_channel.setdefault(contact.channel, []).append(contact.value)

    def joined(channel: Channel) -> str:
        return "\n".join(by_channel.get(channel, []))

    return [
        company.id, company.name, company.sector, company.city, company.google_category or "",
        company.rating or "", company.reviews_count or "", company.status.value, company.score or "",
        company.website or "", company.phone_e164 or "", joined(Channel.EMAIL), joined(Channel.WHATSAPP),
        joined(Channel.INSTAGRAM), joined(Channel.FACEBOOK), joined(Channel.LINKEDIN),
        (company.raw or {}).get("link", ""),
    ]


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
            return self.sheet.add_worksheet(title=title, rows=1000, cols=columns)

    def push(self) -> int:
        """Postgres -> Sheets: pestaña Leads completa. Devuelve cuántas filas escribió."""
        with session_scope() as session:
            rows = [lead_row(c) for c in LeadRepository(session).all_with_contacts()]

        tab = self._tab(LEADS_TAB, len(LEADS_COLUMNS))
        tab.clear()
        tab.update([LEADS_COLUMNS, *rows], "A1")
        tab.freeze(rows=1)
        return len(rows)

    def pull_decisions(self) -> int:
        """Sheets -> Postgres: aprobaciones, ediciones, envíos asistidos y respuestas marcadas a mano."""
        raise NotImplementedError("F2")
