from __future__ import annotations

import pytest

from app.core.errors import InvalidTransition
from app.core.state_machine import (
    ARCHITECTURE_APPROVED,
    ARCHITECTURE_READY,
    AWAITING_ARCHITECTURE_APPROVAL,
    COMPLETED,
    DEPLOYING,
    DRAFT,
    GATHERING_REQUIREMENTS,
    VALIDATED,
    VALIDATING,
    ensure_transition,
)


@pytest.mark.parametrize(
    "current,new",
    [
        (DRAFT, GATHERING_REQUIREMENTS),
        (GATHERING_REQUIREMENTS, ARCHITECTURE_READY),
        (ARCHITECTURE_READY, AWAITING_ARCHITECTURE_APPROVAL),
        (AWAITING_ARCHITECTURE_APPROVAL, ARCHITECTURE_APPROVED),
        (ARCHITECTURE_APPROVED, VALIDATING),
        (VALIDATING, VALIDATED),
        (VALIDATED, "planning"),
        ("planning", "awaiting_deployment_approval"),
        ("awaiting_deployment_approval", DEPLOYING),
        (DEPLOYING, "deployment_successful"),
        ("deployment_successful", COMPLETED),
        (COMPLETED, ARCHITECTURE_READY),
    ],
)
def test_valid_transitions(current: str, new: str) -> None:
    ensure_transition(current, new)


@pytest.mark.parametrize(
    "current,new",
    [
        (DRAFT, COMPLETED),
        (DRAFT, DEPLOYING),
        (VALIDATING, COMPLETED),
        (DEPLOYING, DRAFT),
        ("archived", GATHERING_REQUIREMENTS),
    ],
)
def test_invalid_transitions(current: str, new: str) -> None:
    with pytest.raises(InvalidTransition):
        ensure_transition(current, new)


def test_same_state_is_idempotent() -> None:
    ensure_transition(DRAFT, DRAFT)
    ensure_transition(COMPLETED, COMPLETED)
