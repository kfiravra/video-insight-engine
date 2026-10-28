"""Shared fakes and seed data for the billing route tests.

`FakeDb` serves only `find` / `find_one` on the five collections the billing
view may read; touching any other attribute (a write) raises, so a test that
exercises the route also proves it performs no writes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.routes.billing import get_billing_service
from src.services.billing_account import BillingAccountService
from src.services.billing_reader import BillingReader

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
USER_ID = "a" * 24
USER_OID = ObjectId(USER_ID)
ACCOUNT_OID = ObjectId("b" * 24)
URL = f"/billing/accounts/{USER_ID}"
READ_METHODS = {"find", "find_one"}
BILLING_COLLECTIONS = {"users", "billingAccounts", "ledgerEntries", "creditLots", "recurringGrants"}


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}


# ─── In-memory stand-in for the Mongo collections ───


def _comparable(value: Any) -> Any:
    """Mongo returns naive UTC datetimes; compare everything as naive UTC."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _matches_operator(actual: Any, operator: str, expected: Any) -> bool:
    actual, expected = _comparable(actual), _comparable(expected)
    if operator == "$lt":
        return actual is not None and actual < expected
    if operator == "$gt":
        return actual is not None and actual > expected
    if operator == "$nin":
        return actual not in expected
    raise AssertionError(f"fake collection does not support {operator}")


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            if not all(_matches_operator(actual, op, val) for op, val in expected.items()):
                return False
        elif _comparable(actual) != _comparable(expected):
            return False
    return True


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, spec: list[tuple[str, int]]) -> FakeCursor:
        docs = list(self._docs)
        for key, direction in reversed(spec):
            docs.sort(key=lambda d, k=key: _comparable(d[k]), reverse=direction < 0)
        return FakeCursor(docs)

    def limit(self, count: int) -> FakeCursor:
        return FakeCursor(self._docs[:count])

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        assert length is not None, "to_list(None) loads an unbounded result"
        return self._docs[:length]


class FakeCollection:
    """Serves reads only: touching any other attribute (a write) fails the test."""

    def __init__(self, name: str, docs: list[dict[str, Any]], log: list[str]) -> None:
        self._name, self._docs, self._log = name, docs, log

    def find(self, query: dict[str, Any], projection: Any = None) -> FakeCursor:
        self._log.append(f"{self._name}.find")
        return FakeCursor([d for d in self._docs if _matches(d, query)])

    async def find_one(
        self, query: dict[str, Any], projection: Any = None
    ) -> dict[str, Any] | None:
        self._log.append(f"{self._name}.find_one")
        return next((d for d in self._docs if _matches(d, query)), None)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"billing route touched {self._name}.{name}; reads only")


class FakeDb:
    def __init__(self, **collections: list[dict[str, Any]]) -> None:
        self.calls: list[str] = []
        self._collections = {
            name: FakeCollection(name, collections.get(name, []), self.calls)
            for name in BILLING_COLLECTIONS
        }

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_") or name not in self._collections:
            raise AssertionError(f"billing route touched collection {name}")
        return self._collections[name]


# ─── Seed data ───


def make_account(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": ACCOUNT_OID,
        "ownerType": "user",
        "ownerId": USER_OID,
        "plan": {
            "id": "pro",
            "status": "active",
            "source": "manual",
            "periodStart": datetime(2026, 10, 1),
            "periodEnd": datetime(2027, 10, 1),
        },
        "buckets": {
            "free": {"recentFreeVideoAt": []},
            "plan": {
                "cycleStart": datetime(2026, 10, 1),
                "cycleEnd": datetime(2026, 11, 1),
                "minutesGranted": 600,
                "minutesUsed": 140,
            },
            "credit": {"minutesAvailable": 150},
        },
        "holds": [],
        "caps": {},
        "version": 7,
        "createdAt": datetime(2026, 10, 1),
        "updatedAt": datetime(2026, 10, 27, 8, 30, 15, 123000),
    }
    doc.update(overrides)
    return doc


def make_lot(n: int, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": ObjectId(f"{n:024x}"),
        "accountId": ACCOUNT_OID,
        "minutesGranted": 150,
        "minutesRemaining": 150,
        "source": {"kind": "payment", "ref": f"txn_{n}"},
        "priceUsdCents": 500,
        "expiresAt": datetime(2027, 10, 1),
        "createdAt": datetime(2026, 10, n),
    }
    doc.update(overrides)
    return doc


def make_entry(n: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": ObjectId(f"{0x100 + n:024x}"),
        "accountId": ACCOUNT_OID,
        "userId": USER_OID,
        "type": "grant",
        "bucket": "credit",
        "minutes": 150,
        "source": {"kind": "admin", "ref": "c" * 24},
        "idempotencyKey": f"admin:{n}",
        "createdAt": created_at,
    }
    doc.update(overrides)
    return doc


def make_db(**collections: list[dict[str, Any]]) -> FakeDb:
    collections.setdefault("users", [{"_id": USER_OID, "email": "u@example.com"}])
    return FakeDb(**collections)


UseDb = Callable[["FakeDb"], "FakeDb"]


def install_service(db: FakeDb) -> FakeDb:
    """Serve the route from `db` at the fixed clock `NOW`; callers clear the override after."""
    service = BillingAccountService(BillingReader(db), clock=lambda: NOW)  # type: ignore[arg-type]
    app.dependency_overrides[get_billing_service] = lambda: service
    return db


async def api_get(url: str = URL, headers: dict[str, str] | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=auth_headers() if headers is None else headers)
