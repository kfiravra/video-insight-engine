"""Read-only access to the billing collections.

vie-api's ``BillingService`` is the only writer of ``billingAccounts``,
``ledgerEntries``, ``creditLots`` and ``recurringGrants``. This module only ever
calls ``find`` / ``find_one`` on them: a write from vie-admin would bypass the
single-writer transaction that keeps the ledger and its projections equal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

Document = dict[str, Any]

# Caps mirror vie-api (ADMIN_LOT_LIMIT) and keep one request's work bounded.
RECENT_LOT_LIMIT = 100
OPEN_LOT_LIMIT = 1000
RECURRING_GRANT_LIMIT = 100
# Entries written in the same transaction share a createdAt; far fewer than this.
MAX_TIED_ENTRIES = 500

_LEDGER_SORT = [("createdAt", -1), ("_id", -1)]


@dataclass(frozen=True, slots=True)
class LedgerPage:
    """One page of ledger entries, newest first."""

    entries: list[Document]
    # createdAt to pass as ``before`` for the next page; None on the last page.
    next_before: datetime | None


class BillingReader:
    """Queries the billing collections without ever writing to them."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._users = db.users
        self._accounts = db.billingAccounts
        self._ledger = db.ledgerEntries
        self._lots = db.creditLots
        self._grants = db.recurringGrants

    async def user_exists(self, user_id: ObjectId) -> bool:
        return await self._users.find_one({"_id": user_id}, {"_id": 1}) is not None

    async def find_account(self, user_id: ObjectId) -> Document | None:
        return await self._accounts.find_one({"ownerType": "user", "ownerId": user_id})

    async def open_lots(self, account_id: ObjectId) -> list[Document]:
        """Lots that still hold minutes (expired-but-unswept included; the caller filters)."""
        cursor = self._lots.find({"accountId": account_id, "minutesRemaining": {"$gt": 0}})
        return await cursor.limit(OPEN_LOT_LIMIT).to_list(OPEN_LOT_LIMIT)

    async def recent_lots(self, account_id: ObjectId) -> list[Document]:
        cursor = self._lots.find({"accountId": account_id}).sort([("createdAt", -1), ("_id", -1)])
        return await cursor.limit(RECENT_LOT_LIMIT).to_list(RECENT_LOT_LIMIT)

    async def recurring_grants(self, account_id: ObjectId) -> list[Document]:
        """Live (not soft-deleted) grants, oldest first."""
        cursor = self._grants.find({"accountId": account_id, "deletedAt": None})
        cursor = cursor.sort([("createdAt", 1), ("_id", 1)])
        return await cursor.limit(RECURRING_GRANT_LIMIT).to_list(RECURRING_GRANT_LIMIT)

    async def ledger_page(
        self, account_id: ObjectId, *, limit: int, before: datetime | None
    ) -> LedgerPage:
        """Newest-first page of ``limit`` entries strictly older than ``before``.

        The cursor is a timestamp, so a page never ends inside a run of entries
        sharing one ``createdAt`` (they are written together): the rest of that
        run is appended, otherwise the next page would skip them.
        """
        query: Document = {"accountId": account_id}
        if before is not None:
            query["createdAt"] = {"$lt": before}
        page = await self._ledger.find(query).sort(_LEDGER_SORT).limit(limit).to_list(limit)
        if len(page) < limit:
            return LedgerPage(entries=page, next_before=None)

        last_at = page[-1]["createdAt"]
        page.extend(await self._tied_entries(account_id, last_at, page))
        has_older = await self._ledger.find_one(
            {"accountId": account_id, "createdAt": {"$lt": last_at}}, {"_id": 1}
        )
        return LedgerPage(entries=page, next_before=last_at if has_older else None)

    async def _tied_entries(
        self, account_id: ObjectId, created_at: datetime, page: list[Document]
    ) -> list[Document]:
        seen = [entry["_id"] for entry in page if entry["createdAt"] == created_at]
        query: Document = {"accountId": account_id, "createdAt": created_at, "_id": {"$nin": seen}}
        cursor = self._ledger.find(query).sort(_LEDGER_SORT)
        return await cursor.limit(MAX_TIED_ENTRIES).to_list(MAX_TIED_ENTRIES)
