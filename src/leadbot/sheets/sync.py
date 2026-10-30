"""Google Sheets como interfaz del MVP. Postgres es la fuente de verdad; Sheets es la vista.

Pestañas:
    Leads      solo lectura: empresa, sector, ciudad, score, estado, canales.
    Aprobación editable: mensaje (puedes reescribirlo), columna Decisión (APROBADO / RECHAZADO)
               y Notas. Lo que edites aquí se lee de vuelta a Postgres.
    Respuestas editable: marcas a mano las respuestas de redes (REPLIED / MEETING / WON / LOST / OPT_OUT).
    Métricas   solo lectura: enviados, respuestas y reuniones por canal, sector y ciudad.

Regla de conflicto: Sheets solo puede cambiar las columnas editables. Todo lo demás se sobrescribe
desde Postgres en cada sincronización.
"""

LEADS_TAB = "Leads"
APPROVAL_TAB = "Aprobación"
REPLIES_TAB = "Respuestas"
METRICS_TAB = "Métricas"

APPROVAL_COLUMNS = [
    "message_id", "empresa", "ciudad", "canal", "destinatario", "score", "caso",
    "asunto", "mensaje", "decisión", "notas",
]
EDITABLE_APPROVAL_COLUMNS = {"asunto", "mensaje", "decisión", "notas"}


class SheetsSync:
    def __init__(self, service_account_file: str, sheet_id: str):
        self.service_account_file = service_account_file
        self.sheet_id = sheet_id

    def push(self) -> None:
        """Postgres -> Sheets: leads, mensajes pendientes, métricas."""
        raise NotImplementedError("F1")

    def pull_decisions(self) -> int:
        """Sheets -> Postgres: aprobaciones, ediciones y respuestas marcadas a mano."""
        raise NotImplementedError("F2")
