"""Role rules for the admin panel's login and session checks.

Mirrors ``effectiveRoles`` and ``grantsPermission`` in
``api/src/repositories/user.repository.ts``: a ``roles`` array is
authoritative whenever the user has one (even an empty list), and only a user
with no ``roles`` array falls back to the legacy ``role: "admin"``. Both
implementations are checked against the same cases in
``packages/types/test-vectors/admin-auth.json``, so a change here without the
matching change there fails a test on one side.
"""

from __future__ import annotations

from collections.abc import Mapping

ADMIN_ROLE = "admin"
KNOWN_ROLES = frozenset({ADMIN_ROLE})


def effective_roles(user: Mapping[str, object] | None) -> list[str]:
    """Roles the user effectively holds; unknown values in ``roles`` are dropped."""
    if user is None:
        return []
    roles = user.get("roles")
    if isinstance(roles, list):
        return [role for role in roles if isinstance(role, str) and role in KNOWN_ROLES]
    return [ADMIN_ROLE] if user.get("role") == ADMIN_ROLE else []


def is_active_admin(user: Mapping[str, object] | None) -> bool:
    """True for an admin whose account is not soft-deleted (``deletedAt`` unset or null)."""
    if user is None or user.get("deletedAt"):
        return False
    return ADMIN_ROLE in effective_roles(user)
