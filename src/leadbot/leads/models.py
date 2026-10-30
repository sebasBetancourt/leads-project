"""Modelo de datos (ver docs/DATA_MODEL.md). Postgres es la fuente de verdad; Sheets es solo la vista."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from leadbot.core.db import Base
from leadbot.leads.status import Channel, LeadStatus, MessageStatus


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Company(TimestampMixin, Base):
    """Un negocio encontrado en Google Maps."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[str | None] = mapped_column(String(255), unique=True)  # id de Google Maps
    name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str] = mapped_column(String(64), index=True)  # clave de sectors.yaml
    city: Mapped[str] = mapped_column(String(64), index=True)
    address: Mapped[str | None] = mapped_column(String(500))
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)  # para deduplicar
    website: Mapped[str | None] = mapped_column(String(500))
    google_category: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[float | None]
    reviews_count: Mapped[int | None]
    source_query: Mapped[str | None] = mapped_column(String(255))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # fila original de gosom

    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus, native_enum=False), default=LeadStatus.NEW, index=True)
    score: Mapped[int | None]  # 0-100, lo pone el agente

    contacts: Mapped[list["ContactChannel"]] = relationship(back_populates="company")
    research: Mapped[list["Research"]] = relationship(back_populates="company")
    messages: Mapped[list["Message"]] = relationship(back_populates="company")


class ContactChannel(TimestampMixin, Base):
    """Una forma de contactar al negocio: un correo, un número de WhatsApp, un perfil de Instagram..."""

    __tablename__ = "contact_channels"
    __table_args__ = (UniqueConstraint("company_id", "channel", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False))
    value: Mapped[str] = mapped_column(String(500))  # email, +57..., URL del perfil
    source: Mapped[str] = mapped_column(String(64))  # gmaps | website | manual
    verified: Mapped[bool] = mapped_column(default=False)

    company: Mapped[Company] = relationship(back_populates="contacts")


class Research(TimestampMixin, Base):
    """Resultado del agente para un lead: análisis, necesidad detectada, score y caso elegido."""

    __tablename__ = "research"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text)
    needs: Mapped[list[str]] = mapped_column(JSON, default=list)
    score: Mapped[int]
    qualified: Mapped[bool]
    case_study: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))  # qué LLM lo generó
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # calificación, señales e investigación
    tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(default=0.0, server_default="0")

    company: Mapped[Company] = relationship(back_populates="research")


class Message(TimestampMixin, Base):
    """Un mensaje por canal. Pasa por aprobación antes de enviarse."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_company_status", "company_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact_channels.id"))
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False), index=True)
    step: Mapped[int] = mapped_column(default=0)  # 0 = primer contacto, 1+ = seguimientos
    subject: Mapped[str | None] = mapped_column(String(255))  # solo email
    body: Mapped[str] = mapped_column(Text)
    original_body: Mapped[str | None] = mapped_column(Text)  # lo que redactó el agente, antes de tus ediciones
    review_flags: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default="[]"
    )  # alertas de agents/checks.py
    attachment_path: Mapped[str | None] = mapped_column(String(500))  # propuesta PDF
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, native_enum=False), default=MessageStatus.DRAFT, index=True
    )
    decision: Mapped[str | None] = mapped_column(String(16))  # APROBADO | REHACER | DESCARTAR (Sheets)
    reviewer_notes: Mapped[str | None] = mapped_column(Text)  # tus comentarios desde Sheets
    scheduled_for: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship(back_populates="messages")
    contact: Mapped[ContactChannel] = relationship()


class Event(Base):
    """Bitácora de todo lo que pasa con un lead (auditoría, métricas)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(64), index=True)  # status_changed, sent, replied...
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class Suppression(Base):
    """Lista de no contactar (Ley 1581). Se revisa antes de cada envío, por valor exacto."""

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(String(500), unique=True)  # email, teléfono o URL
    reason: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Job(Base):
    """Cola de trabajos en Postgres (SELECT ... FOR UPDATE SKIP LOCKED), sin broker externo."""

    __tablename__ = "jobs"
    __table_args__ = (
        # Un mismo trabajo (ej. calificar el lead 42) no se encola dos veces mientras esté vivo.
        Index(
            "uq_jobs_alive",
            "kind",
            "dedup_key",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)  # discover, enrich, qualify, send
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedup_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|running|done|failed
    attempts: Mapped[int] = mapped_column(default=0)
    run_after: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    locked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
