"""The admin role rule, held to the same cases as vie-api's TypeScript implementation."""

from __future__ import annotations

from typing import Any

import pytest

from src.roles import effective_roles, is_active_admin
from tests._vectors import VECTORS

ROLE_CASES: list[dict[str, Any]] = VECTORS["roleRules"]


@pytest.mark.parametrize("case", ROLE_CASES, ids=[case["name"] for case in ROLE_CASES])
def test_effective_roles_matches_the_shared_case(case: dict[str, Any]) -> None:
    assert effective_roles(case["user"]) == case["effectiveRoles"]


@pytest.mark.parametrize("case", ROLE_CASES, ids=[case["name"] for case in ROLE_CASES])
def test_is_active_admin_matches_the_shared_case(case: dict[str, Any]) -> None:
    assert is_active_admin(case["user"]) is case["activeAdmin"]


def test_a_missing_user_holds_no_roles() -> None:
    assert effective_roles(None) == []


def test_a_missing_user_is_not_an_admin() -> None:
    assert is_active_admin(None) is False


def test_the_shared_cases_cover_the_precedence_rule() -> None:
    """Guards the vector file itself: an empty roles array must beat a legacy role."""
    assert "empty roles overrides a leftover legacy role" in {case["name"] for case in ROLE_CASES}
