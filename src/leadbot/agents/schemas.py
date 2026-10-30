"""Salidas estructuradas del LLM (patrón structured_outputs.py de kaymen99). Todo se valida con Pydantic.

Sin valores por defecto: la salida estructurada estricta (Groq, OpenAI) exige que todo campo sea
obligatorio; lo opcional se declara `X | None` sin default.
"""

from pydantic import BaseModel, Field

from leadbot.leads.status import Channel


class DigitalPresence(BaseModel):
    has_website: bool
    has_online_store: bool
    has_payment_gateway: bool
    sells_via_whatsapp_or_social: bool
    uses_third_party_platform: bool = Field(description="Shopify, Wix, Rappi, Mercado Libre...")
    website_outdated: bool
    notes: str


class LeadQualification(BaseModel):
    summary: str = Field(description="2-3 frases sobre el negocio")
    presence: DigitalPresence
    needs: list[str] = Field(description="Necesidades concretas que BrainSol puede resolver")
    score: int = Field(ge=0, le=100)
    qualified: bool
    reason: str = Field(description="Por qué califica o no")
    case_study: str | None = Field(description="Nombre del caso de knowledge/ más parecido")
    best_channels: list[Channel] = Field(description="Canales en orden de preferencia")


class DraftedMessage(BaseModel):
    """Texto de un mensaje para un canal (el canal y el destinatario los pone el código)."""

    subject: str | None = Field(description="Asunto, solo para email; null en redes")
    body: str = Field(description="Cuerpo del mensaje, listo para enviar")
