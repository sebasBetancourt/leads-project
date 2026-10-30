from itertools import pairwise

import pytest

from leadbot.leads.status import (
    LEAD_TRANSITIONS,
    InvalidTransition,
    LeadStatus,
    can_transition,
    ensure_transition,
)


def test_every_status_has_transitions_defined():
    assert set(LEAD_TRANSITIONS) == set(LeadStatus)


def test_happy_path():
    path = [
        LeadStatus.NEW, LeadStatus.ENRICHED, LeadStatus.QUALIFIED, LeadStatus.DRAFTED,
        LeadStatus.PENDING_APPROVAL, LeadStatus.APPROVED, LeadStatus.CONTACTED,
        LeadStatus.REPLIED, LeadStatus.MEETING, LeadStatus.WON,
    ]
    for current, target in pairwise(path):
        assert ensure_transition(current, target) == target


def test_cannot_contact_without_approval():
    assert not can_transition(LeadStatus.DRAFTED, LeadStatus.CONTACTED)
    with pytest.raises(InvalidTransition):
        ensure_transition(LeadStatus.QUALIFIED, LeadStatus.APPROVED)


def test_opt_out_from_anywhere_and_final():
    for status in LeadStatus:
        if status != LeadStatus.OPT_OUT:
            assert can_transition(status, LeadStatus.OPT_OUT)
    for status in LeadStatus:
        assert not can_transition(LeadStatus.OPT_OUT, status)
