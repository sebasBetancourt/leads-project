from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from leadbot.leads.status import Channel
from leadbot.outreach.guardrails import check_send, daily_cap, load_policy

BOGOTA = ZoneInfo("America/Bogota")
TUESDAY_10AM = datetime(2026, 11, 3, 10, 0, tzinfo=BOGOTA)


@pytest.fixture
def policy():
    return load_policy()


def ok_args(**overrides):
    args = dict(
        now=TUESDAY_10AM, sent_today=0, days_active=0, paused=False, suppressed=False, body="Hola"
    )
    return args | overrides


def test_allows_normal_send(policy):
    assert check_send(policy, Channel.WHATSAPP, **ok_args()).allowed


@pytest.mark.parametrize(
    "overrides",
    [
        {"paused": True},
        {"suppressed": True},
        {"now": datetime(2026, 11, 7, 10, 0, tzinfo=BOGOTA)},  # sábado
        {"now": datetime(2026, 11, 3, 19, 0, tzinfo=BOGOTA)},  # 7pm
        {"sent_today": 15},
    ],
)
def test_blocks(policy, overrides):
    assert not check_send(policy, Channel.WHATSAPP, **ok_args(**overrides)).allowed


def test_window_uses_bogota_time(policy):
    utc_3pm = datetime(2026, 11, 3, 15, 0, tzinfo=ZoneInfo("UTC"))  # 10am en Bogotá
    assert check_send(policy, Channel.EMAIL, **ok_args(now=utc_3pm)).allowed


def test_email_warmup(policy):
    limits = policy.channels[Channel.EMAIL]
    assert daily_cap(limits, 0) == 30
    assert daily_cap(limits, 10) == 60
    assert daily_cap(limits, 100) == 80


def test_linkedin_note_length(policy):
    assert not check_send(policy, Channel.LINKEDIN, **ok_args(body="x" * 301)).allowed
