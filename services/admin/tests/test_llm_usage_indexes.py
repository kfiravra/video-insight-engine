"""The admin and vie-api build the same ``llm_usage`` run/session indexes (D9).

Whichever service boots first builds them and the other's call is a no-op, so
the two specs must stay identical; a drift would build a second index.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import main

_API_MONGO_PLUGIN = Path(__file__).resolve().parents[3] / "api" / "src" / "plugins" / "mongodb.ts"


@pytest.mark.parametrize("field", ["run_id", "session_id"])
def test_should_build_the_same_index_as_the_api(field: str) -> None:
    if not _API_MONGO_PLUGIN.exists():
        pytest.skip("vie-api sources are not in this checkout")

    admin_source = Path(main.__file__).read_text()
    api_source = _API_MONGO_PLUGIN.read_text()

    assert f'create_index([("{field}", 1), ("timestamp", -1)])' in admin_source
    assert f"{{ key: {{ {field}: 1, timestamp: -1 }} }}" in api_source
