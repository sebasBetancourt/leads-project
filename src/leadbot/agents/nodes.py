"""Nodos del grafo. Cada uno recibe el estado y devuelve solo las claves que cambia.

La investigación es determinista (ADR 0006): los nodos deciden qué tool llamar y con qué
argumentos; el LLM solo califica y redacta. Así el costo por lead es predecible y repetible.
"""

import logging
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from leadbot.agents.checks import check_message, ensure_opt_out
from leadbot.agents.schemas import DraftedMessage, LeadQualification
from leadbot.agents.state import AgentState, MessageDraft
from leadbot.core import llm
from leadbot.core.config import KNOWLEDGE_DIR, get_settings, load_yaml
from leadbot.core.db import session_scope
from leadbot.enrichment.pipeline import NOT_OWN_SITE_HOSTS, SOCIAL_HOSTS
from leadbot.enrichment.signals import SiteSignals, detect_signals, find_shop_link
from leadbot.leads.repository import LeadRepository
from leadbot.leads.status import Channel
from leadbot.tools.base import Tool
from leadbot.tools.knowledge_base import load_case_studies

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_QUALIFY = (
    "Eres un analista comercial de BrainSol. Evalúas negocios colombianos con datos verificables y "
    "respondes solo con JSON válido."
)
SYSTEM_DRAFT = (
    "Eres quien escribe los primeros mensajes comerciales de BrainSol. Escribes como una persona, "
    "en español de Colombia, y respondes solo con JSON válido."
)
# Orden de preferencia cuando el modelo no elige canales válidos.
CHANNEL_PREFERENCE = [Channel.EMAIL, Channel.WHATSAPP, Channel.INSTAGRAM, Channel.FACEBOOK, Channel.LINKEDIN]
CHANNEL_RULES = {
    Channel.EMAIL: "Email. Incluye un asunto corto (máx. 8 palabras), sin mayúsculas sostenidas ni signos de "
    "exclamación. Cuerpo de 80 a 140 palabras, en 3 o 4 párrafos cortos. Firma con el nombre del remitente.",
    Channel.WHATSAPP: "WhatsApp. Sin asunto. Máximo 70 palabras, en 2 o 3 líneas. Saluda por el nombre del negocio.",
    Channel.INSTAGRAM: "Mensaje directo de Instagram. Sin asunto. Máximo 60 palabras. Menciona algo de su perfil "
    "solo si aparece en la investigación.",
    Channel.FACEBOOK: "Mensaje de Facebook Messenger a su página. Sin asunto. Máximo 70 palabras.",
    Channel.LINKEDIN: "Mensaje de LinkedIn. Sin asunto. Máximo {max_chars} caracteres en total, contando la "
    "línea de baja.",
}


FOLLOW_UP_HINTS = {
    False: "Aporta un ángulo nuevo en una frase (un beneficio concreto para SU negocio); no repitas el primer mensaje.",
    True: "Es el último mensaje: cierra con cortesía, deja la puerta abierta y no insistas más.",
}


def untrusted(label: str, text: str) -> str:
    """Delimita contenido de terceros para que el modelo lo trate como datos (prompt injection)."""
    return f"{label}:\n<<<\n{text.replace('<<<', '').replace('>>>', '')}\n>>>"


def _host(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")


class LeadAgentNodes:
    def __init__(self, tools: dict[str, Tool], *, dry_run: bool = False):
        self.tools = tools
        self.dry_run = dry_run  # True: no escribe en Postgres (leadbot qualify --dry-run)
        self.sectors = load_yaml("sectors")["sectors"]
        self.limits = load_yaml("limits")
        self.agent_cfg = self.limits["agent"]
        self.cases = {c.name: c for c in load_case_studies()}
        self.brainsol = (KNOWLEDGE_DIR / "brainsol.md").read_text(encoding="utf-8")

    # --- Investigación -------------------------------------------------------------------

    def review_website(self, state: AgentState) -> AgentState:
        """Usa web_reader sobre la web del lead: ¿tienda en línea?, ¿pagos?, ¿desactualizada?"""
        website = state["lead"].get("website")
        if not website:
            return {"research_notes": ["Web: no tiene web registrada en Google Maps."], "signals": {}}
        host = _host(website)
        if any(host.endswith(h) for h in SOCIAL_HOSTS):
            return {
                "research_notes": [f"Web: en Google Maps su web es una red social ({website}); no tiene web propia."],
                "signals": {},
            }
        if any(host.endswith(h) for h in NOT_OWN_SITE_HOSTS):
            return {
                "research_notes": [
                    f"Web: en Google Maps aparece {website}, que es un directorio o una marca, no su web."
                ],
                "signals": {},
            }

        home = self.tools["web_reader"](url=website, max_chars=self.agent_cfg["website_chars"])
        if not home.ok:
            return {"research_notes": [f"Web: {home.content}"], "signals": {}}

        pages = {home.data["url"]: home.data["html"]}
        notes = [untrusted(f"Texto de su web ({home.data['url']})", home.content)]
        if shop_url := find_shop_link(home.data["html"], home.data["url"]):
            shop = self.tools["web_reader"](url=shop_url, max_chars=self.agent_cfg["shop_page_chars"])
            if shop.ok:
                pages[shop.data["url"]] = shop.data["html"]
                notes.append(untrusted(f"Página de tienda o catálogo ({shop.data['url']})", shop.content))

        signals = detect_signals(pages)
        notes.insert(0, f"Señales detectadas en su web (verificadas por código):\n{signals.summary()}")
        return {"research_notes": notes, "signals": signals.model_dump()}

    def search_web_presence(self, state: AgentState) -> AgentState:
        """Usa web_search: reseñas, marketplaces, domicilios, noticias."""
        if "web_search" not in self.tools:
            return {"research_notes": []}
        lead = state["lead"]
        queries = [
            f'"{lead["name"]}" {lead["city"]}',
            f'"{lead["name"]}" {lead["city"]} rappi OR "mercado libre" OR domicilios OR tienda online',
        ]
        notes, failures = [], []
        for query in queries:
            result = self.tools["web_search"](query=query, max_results=self.agent_cfg["search_results"])
            if result.ok:
                notes.append(untrusted(f"Resultados de búsqueda para {query}", result.content))
            else:
                failures.append(result.content)
        if not notes:
            notes.append(f"Búsqueda web: no disponible ({'; '.join(failures)[:200]}).")
        return {"research_notes": notes}

    # --- Calificación --------------------------------------------------------------------

    def _available_channels(self, state: AgentState) -> list[Channel]:
        channels = state["lead"]["channels"]
        return [c for c in CHANNEL_PREFERENCE if channels.get(c.value)]

    def _sector_cases(self, sector: str) -> list[str]:
        names = self.sectors.get(sector, {}).get("case_studies", [])
        return [n for n in names if n in self.cases] or list(self.cases)

    @staticmethod
    def _lead_text(state: AgentState) -> str:
        lead = state["lead"]
        lines = [
            f"Nombre: {lead['name']}",
            f"Ciudad: {lead['city']}",
            f"Categoría en Google Maps: {lead.get('google_category') or 'sin dato'}",
            f"Calificación en Google Maps: {lead.get('rating') or 'sin dato'} "
            f"({lead.get('reviews_count') or 0} reseñas)",
            f"Web: {lead.get('website') or 'no tiene'}",
            "Canales de contacto: "
            + (", ".join(f"{c} ({len(v)})" for c, v in lead["channels"].items() if v) or "ninguno"),
        ]
        return "\n".join(lines)

    def qualify(self, state: AgentState) -> AgentState:
        """LLM con salida LeadQualification, con prompts/qualify.md + sectors.yaml + knowledge_base."""
        lead = state["lead"]
        sector = self.sectors.get(lead["sector"], {})
        available = self._available_channels(state)
        case_names = self._sector_cases(lead["sector"])
        prompt = (
            (PROMPTS_DIR / "qualify.md")
            .read_text(encoding="utf-8")
            .format(
                brainsol=self.brainsol,
                sector_label=sector.get("label", lead["sector"]),
                sector_pains="; ".join(sector.get("pains", [])) or "sin dato",
                sector_offer=sector.get("offer", "Tienda en línea o software a medida"),
                case_studies="\n\n".join(f"### {n}\n{self.cases[n].body}" for n in case_names),
                lead=self._lead_text(state),
                research_notes="\n\n".join(state.get("research_notes", [])) or "Sin investigación disponible.",
                case_study_names=", ".join(case_names),
                min_score=self.agent_cfg["min_score"],
                available_channels=", ".join(c.value for c in available) or "(ninguno)",
            )
        )
        result = llm.complete_structured(SYSTEM_QUALIFY, prompt, LeadQualification, task="qualify")
        return {
            "qualification": self._enforce_rules(result.value, available, case_names),
            "usage": [asdict(result.usage)],
        }

    def _enforce_rules(
        self, q: LeadQualification, available: list[Channel], case_names: list[str]
    ) -> LeadQualification:
        """Lo que el código garantiza aunque el modelo se equivoque: canales reales, caso válido, umbral."""
        max_channels = self.limits["max_channels_per_lead"]
        chosen = [c for c in dict.fromkeys(q.best_channels) if c in available]
        if not chosen:
            chosen = available
        q.best_channels = chosen[:max_channels]

        if q.case_study not in self.cases:
            q.case_study = None
        q.qualified = q.score >= self.agent_cfg["min_score"] and bool(q.best_channels)
        if q.qualified and q.case_study is None:
            q.case_study = case_names[0]
        if not q.best_channels and q.score >= self.agent_cfg["min_score"]:
            q.reason += " (No califica: no tiene un canal de contacto disponible.)"
        return q

    # --- Redacción -----------------------------------------------------------------------

    def _channel_rules(self, channel: Channel) -> str:
        return CHANNEL_RULES[channel].format(max_chars=self._max_chars(channel))

    def _max_chars(self, channel: Channel) -> int | None:
        if channel == Channel.LINKEDIN:
            return self.limits["channels"]["linkedin"].get("max_message_chars")
        return self.agent_cfg.get("max_body_chars", {}).get(channel.value)

    def draft_messages(self, state: AgentState) -> AgentState:
        """Un mensaje por cada canal de best_channels (máx. max_channels_per_lead)."""
        lead, q = state["lead"], state["qualification"]
        settings = get_settings()
        case = self.cases.get(q.case_study or "")
        case_text = f"{case.name} ({case.url})\n{case.body}" if case else "Ninguno: no menciones casos."
        research = "\n\n".join(state.get("research_notes", []))
        sources = f"{research}\n{case_text}\n{self._lead_text(state)}"
        feedback = state.get("feedback") or []
        feedback_text = (
            "\n## Correcciones del revisor (aplícalas todas)\n" + "\n".join(f"- {f}" for f in feedback)
            if feedback
            else ""
        )
        qualification = f"{q.summary}\nNecesidades: {'; '.join(q.needs)}\nPresencia digital: {q.presence.notes}"

        drafts, usage = [], []
        for channel in state.get("redraft_channels") or q.best_channels:
            recipients = lead["channels"].get(channel.value) or []
            if not recipients:
                continue
            prompt = (
                (PROMPTS_DIR / "draft_message.md")
                .read_text(encoding="utf-8")
                .format(
                    sender_name=settings.sender_name,
                    lead_name=lead["name"],
                    city=lead["city"],
                    qualification=qualification,
                    research_notes=research or "Sin investigación.",
                    case_study=case_text,
                    channel=channel.value,
                    channel_rules=self._channel_rules(channel),
                    feedback=feedback_text,
                )
            )
            draft, flags = self._draft_one(prompt, channel, sources, usage)
            drafts.append(
                MessageDraft(
                    channel=channel,
                    recipient=recipients[0],
                    subject=draft.subject if channel == Channel.EMAIL else None,
                    body=draft.body,
                    flags=flags,
                )
            )
        return {"drafts": drafts, "usage": usage}

    def _draft_one(
        self, prompt: str, channel: Channel, sources: str, usage: list[dict], *, fixed_subject: str | None = None
    ) -> tuple[DraftedMessage, list[str]]:
        """Redacta, revisa en código y, si hay alertas, pide una corrección (una sola vez)."""
        flags: list[str] = []
        draft = None
        for attempt in range(2):
            if attempt:
                prompt += "\n\n## Corrige estos problemas del borrador anterior\n" + "\n".join(f"- {f}" for f in flags)
            result = llm.complete_structured(SYSTEM_DRAFT, prompt, DraftedMessage, task="draft", temperature=0.7)
            usage.append(asdict(result.usage))
            draft = result.value
            if fixed_subject is not None:
                draft.subject = fixed_subject
            draft.body = ensure_opt_out(draft.body)
            flags = check_message(
                channel, draft.subject, draft.body, sources=sources, max_chars=self._max_chars(channel)
            )
            if not flags:
                break
        return draft, flags

    def draft_follow_up(self, state: AgentState) -> AgentState:
        """Seguimiento por email en el mismo hilo: reutiliza la investigación guardada, sin buscar nada nuevo."""
        lead, q, fu = state["lead"], state["qualification"], state["follow_up"]
        case = self.cases.get(q.case_study or "")
        case_text = f"{case.name} ({case.url})\n{case.body}" if case else "Ninguno: no menciones casos."
        research = "\n\n".join(state.get("research_notes", []))
        previous = "\n\n".join(f"Mensaje {i}:\n{text}" for i, text in enumerate(fu["previous"]))
        sources = f"{research}\n{case_text}\n{self._lead_text(state)}\n{previous}"
        feedback = state.get("feedback") or []
        feedback_text = (
            "\n## Correcciones del revisor (aplícalas todas)\n" + "\n".join(f"- {f}" for f in feedback)
            if feedback
            else ""
        )
        last = fu["step"] >= len(self.limits["sequences"]["email"]["follow_up_days"])
        prompt = (
            (PROMPTS_DIR / "follow_up.md")
            .read_text(encoding="utf-8")
            .format(
                step=fu["step"],
                sender_name=get_settings().sender_name,
                lead_name=lead["name"],
                city=lead["city"],
                qualification=f"{q.summary}\nNecesidades: {'; '.join(q.needs)}\nPresencia digital: {q.presence.notes}",
                research_notes=research or "Sin investigación.",
                case_study=case_text,
                previous=previous,
                step_hint=FOLLOW_UP_HINTS[last],
                feedback=feedback_text,
            )
        )
        usage: list[dict] = []
        draft, flags = self._draft_one(prompt, Channel.EMAIL, sources, usage, fixed_subject=fu["subject"])
        message = MessageDraft(
            channel=Channel.EMAIL, recipient=fu["recipient"], subject=fu["subject"], body=draft.body, flags=flags
        )
        return {"drafts": [message], "usage": usage}

    def save_follow_up(self, state: AgentState) -> AgentState:
        """Guarda el seguimiento en PENDING_APPROVAL. El lead sigue en CONTACTED."""
        if self.dry_run:
            return {}
        usage = state.get("usage", [])
        fu = state["follow_up"]
        with session_scope() as session:
            LeadRepository(session).save_follow_up(
                company_id=state["lead"]["company_id"],
                contact_id=fu["contact_id"],
                step=fu["step"],
                draft=state["drafts"][0],
                research_id=state["research_id"],
                tokens=sum(u["prompt_tokens"] + u["completion_tokens"] for u in usage),
                cost_usd=sum(u["cost_usd"] for u in usage),
                redo_message_id=fu["redo_message_id"],
            )
        return {}

    # --- Persistencia --------------------------------------------------------------------

    def save(self, state: AgentState) -> AgentState:
        """Guarda Research y Messages (PENDING_APPROVAL) y mueve el lead de estado."""
        if self.dry_run:
            return {}
        q = state["qualification"]
        usage = state.get("usage", [])
        models = sorted({u["model"] for u in usage})
        with session_scope() as session:
            LeadRepository(session).save_agent_result(
                company_id=state["lead"]["company_id"],
                qualification=q,
                drafts=state.get("drafts", []),
                research_id=state.get("research_id"),
                research_data={
                    "qualification": q.model_dump(mode="json"),
                    "signals": state.get("signals") or SiteSignals().model_dump(),
                    "research_notes": state.get("research_notes", []),
                },
                model=", ".join(models) or "desconocido",
                tokens=sum(u["prompt_tokens"] + u["completion_tokens"] for u in usage),
                cost_usd=sum(u["cost_usd"] for u in usage),
            )
        return {}

    @staticmethod
    def route_after_qualify(state: AgentState) -> str:
        return "qualified" if state["qualification"].qualified else "not_qualified"
