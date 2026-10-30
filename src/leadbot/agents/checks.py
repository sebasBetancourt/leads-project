"""Revisiones de cada mensaje después del LLM, en código (no se le pide al modelo que se revise solo).

- La línea de baja (Ley 1581) se agrega siempre si falta: no depende del modelo.
- Lo demás produce alertas: si las hay, el agente pide una corrección una vez; si persisten,
  el mensaje llega a Sheets con la columna "alertas" llena para que lo revises con cuidado.
"""

import re
import unicodedata

from leadbot.leads.status import Channel

OPT_OUT_LINE = "Si no te interesa, respóndeme NO y no te vuelvo a escribir."

EMOJI_RE = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff\U0001f900-\U0001f9ff⭐✅]")
PLACEHOLDER_RE = re.compile(r"\[[^\]]{2,40}\]|\{[^}]{2,40}\}|<[A-ZÁÉÍÓÚ_ ]{3,30}>|X{3,}")
# Cifras que el modelo suele inventar: porcentajes, "3x", montos en pesos y "N clientes/ventas/pedidos".
CLAIM_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s?%|\b\d+\s?x\b|\$\s?\d[\d.,]*|\b\d+\s+(?:clientes|ventas|pedidos|tiendas|negocios|años)",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def has_opt_out(body: str) -> bool:
    return "no te vuelvo a escribir" in _normalize(body)


def ensure_opt_out(body: str) -> str:
    body = body.rstrip()
    return body if has_opt_out(body) else f"{body}\n\n{OPT_OUT_LINE}"


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def unsupported_claims(body: str, sources: str) -> list[str]:
    """Cifras del mensaje que no aparecen en la investigación ni en los casos de éxito."""
    source_digits = {_digits(m) for m in CLAIM_RE.findall(sources)}
    source_numbers = set(re.findall(r"\d+", sources))
    claims = []
    for claim in CLAIM_RE.findall(body):
        digits = _digits(claim)
        if digits and digits not in source_digits and digits not in source_numbers:
            claims.append(claim.strip())
    return claims


def check_message(
    channel: Channel,
    subject: str | None,
    body: str,
    *,
    sources: str,
    max_chars: int | None,
) -> list[str]:
    """Devuelve las alertas del mensaje (vacío = listo para tu revisión normal)."""
    flags = []
    if channel == Channel.EMAIL and not (subject or "").strip():
        flags.append("falta el asunto")
    if max_chars and len(body) > max_chars:
        flags.append(f"muy largo: {len(body)} caracteres (máximo {max_chars})")
    emojis = EMOJI_RE.findall(body)
    if channel == Channel.EMAIL and emojis:
        flags.append("tiene emojis (no van en email)")
    elif len(emojis) > 1:
        flags.append(f"tiene {len(emojis)} emojis (máximo uno en redes)")
    if placeholders := PLACEHOLDER_RE.findall(f"{subject or ''} {body}"):
        flags.append(f"tiene marcadores sin reemplazar: {', '.join(placeholders[:3])}")
    if claims := unsupported_claims(body, sources):
        flags.append(f"cifras que no están en la investigación: {', '.join(claims[:3])}")
    return flags
