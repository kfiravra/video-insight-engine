"""Loads the golden vectors shared with vie-api's vitest suites.

``packages/types/test-vectors/admin-auth.json`` holds the actor-header vectors
and the admin role cases. api (vitest) and vie-admin (pytest) read the same
file, so the TypeScript and Python implementations cannot drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_VECTORS_PATH = (
    Path(__file__).resolve().parents[3] / "packages" / "types" / "test-vectors" / "admin-auth.json"
)

VECTORS: dict[str, Any] = json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))
