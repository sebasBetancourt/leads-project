"""Protecciones de envío (patrón send guardrails de AgentSDR). Funciones puras: fáciles de probar.

Todo mensaje pasa por `check_send` justo antes de salir. Si no pasa, vuelve a la cola.
"""

import random
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from leadbot.core.config import CONFIG_DIR, load_yaml
from leadbot.leads.status import Channel


@dataclass(frozen=True)
class ChannelLimits:
    daily_max: int
    min_delay_seconds: int
    max_delay_seconds: int
    warmup_start: int | None = None
    warmup_step: int = 0
    max_message_chars: int | None = None


@dataclass(frozen=True)
class SendPolicy:
    timezone: ZoneInfo
    weekdays: frozenset[int]
    start: time
    end: time
    channels: dict[Channel, ChannelLimits]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def load_policy(config_dir: Path = CONFIG_DIR) -> SendPolicy:
    cfg = load_yaml("limits", config_dir)
    window = cfg["send_window"]
    return SendPolicy(
        timezone=ZoneInfo(cfg["timezone"]),
        weekdays=frozenset(window["weekdays"]),
        start=time.fromisoformat(window["start"]),
        end=time.fromisoformat(window["end"]),
        channels={Channel(k): ChannelLimits(**v) for k, v in cfg["channels"].items()},
    )


def daily_cap(limits: ChannelLimits, days_active: int) -> int:
    """Con calentamiento: warmup_start + warmup_step por día, sin pasar de daily_max."""
    if limits.warmup_start is None:
        return limits.daily_max
    return min(limits.daily_max, limits.warmup_start + limits.warmup_step * days_active)


def in_send_window(policy: SendPolicy, now: datetime) -> bool:
    local = now.astimezone(policy.timezone)
    return local.weekday() in policy.weekdays and policy.start <= local.time() < policy.end


def check_send(
    policy: SendPolicy,
    channel: Channel,
    *,
    now: datetime,
    sent_today: int,
    days_active: int,
    paused: bool,
    suppressed: bool,
    body: str,
) -> Decision:
    if paused:
        return Decision(False, f"canal {channel} en pausa (PAUSE_*)")
    if suppressed:
        return Decision(False, "destinatario en lista de no contactar")
    if not in_send_window(policy, now):
        return Decision(False, "fuera del horario de envío")
    limits = policy.channels[channel]
    if sent_today >= daily_cap(limits, days_active):
        return Decision(False, "tope diario alcanzado")
    if limits.max_message_chars and len(body) > limits.max_message_chars:
        return Decision(False, f"mensaje supera {limits.max_message_chars} caracteres")
    return Decision(True)


def next_delay_seconds(limits: ChannelLimits, rng: random.Random | None = None) -> int:
    """Pausa aleatoria entre envíos del mismo canal, para no enviar a ritmo de máquina."""
    return (rng or random).randint(limits.min_delay_seconds, limits.max_delay_seconds)
