# GDPR Cascade Deletion — Engineer Runbook

> User-facing summary: [`PRIVACY.md`](./PRIVACY.md). Inventory of what gets
> deleted vs. what stays: [`reports/user-data-inventory.md`](../reports/user-data-inventory.md).

## Goals

GDPR Article 17 (right to erasure) requires that when a user requests deletion, we **remove every piece of their personal data** from our live systems, with a documented audit trail. This document is the engineer's view of how that works in the codebase.

## Architecture at a glance

```
                   ┌──────────────────────────────────┐
DELETE /api/users/me ──┐                              │
                        ▼                             │
              userDeletionService.requestDeletion()   │  ← Phase 2: soft delete
                        │                             │
                        ▼                             │
            users.deletedAt = now, hardDeleteAt = +30d
                        │                             │
                        │ (30-day grace window —      │
                        │  user can call /restore     │
                        │  or admin can override)     │
                        │                             │
                        ▼                             │
   billing repair job (every 5 min) / POST /internal/run-deletions │  ← Phase 4: sweep
                        │                             │
                        ▼                             │
              userDeletionService.runScheduledDeletions()
                        │                             │
                        ▼                             │
              executeHardDelete() — the saga          │  ← Phase 3: cascade
                        │                             │
              ┌─────────┼──────────────────┐          │
              ▼         ▼                  ▼          │
        agentNotes  userVideos         userCosts      │
              ▼         ▼                  ▼          │
        folders    idempotencyKeys   userCostAdjustments
              ▼         ▼                  ▼          │
          Qdrant      S3              users (last)    │
              │         │                  │          │
              └─────────┴──────────────────┘          │
                        │                             │
                        ▼                             │
              userDeletions audit row written         │  ← Phase 6: audit log
                                                      │
DELETE /api/admin/users/:id?immediate=true ──────────┘  ← Phase 5: admin escape hatch
   (bypasses the 30-day grace, runs the saga synchronously)
```

## Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `DELETE /api/users/me` | JWT | Self-service soft delete. Returns 202 + `scheduledHardDeleteAt`. |
| `POST /api/users/me/restore` | JWT (bypasses soft-delete check) | Cancel a pending deletion within 30 days. |
| `DELETE /api/admin/users/:id?immediate=true` | `x-admin-key` + signed `x-admin-actor` (`users:delete`) | Run the cascade now (compliance escape hatch). Writes an `adminAudit` row. |
| `DELETE /api/admin/users/:id` (no `immediate`) | `x-admin-key` + signed `x-admin-actor` (`users:delete`) | Schedule the same soft delete as the user-initiated flow, with the admin stored as its origin. Writes an `adminAudit` row at request time. |
| `POST /internal/run-deletions` | `x-internal-secret` header | Manual sweep entrypoint — sweeps `hardDeleteAt <= now` and runs the cascade for each. The same sweep runs on a schedule inside vie-api (see "Running the sweep"). |
| `DELETE /api/admin/videos/:youtubeId` | `x-admin-key` + signed `x-admin-actor` (`videos:purge`) | Not GDPR: the **video** cascade (`VideoCascadeService`), which removes one video from every store for every user. Listed here because it is the only path that deletes the shared rows the user cascade keeps. |

## Key files

| File | Role |
|---|---|
| `api/src/services/user-deletion.service.ts` | Saga orchestrator |
| `api/src/repositories/user.repository.ts` | `markSoftDeleted`, `clearSoftDelete`, `findExpiredSoftDeletes`, `hardDelete` |
| `api/src/repositories/user-deletion.repository.ts` | Audit row writer |
| `api/src/routes/users.routes.ts` | `DELETE /api/users/me`, restore, admin endpoints |
| `api/src/plugins/jwt.ts` | Per-request soft-delete check — kills active sessions immediately |
| `reports/user-data-inventory.md` | Source of truth for what's in/out of scope |

## The cascade (saga)

Steps run in `executeHardDelete()` in this exact order:

1. **`agentNotes`** (assistant service — shared MongoDB)
2. **`userVideos`** (library entries)
3. **`folders`**
4. **`userCosts`**
5. **`userCostAdjustments`**
6. **`idempotencyKeys`**
7. **`videoSummaryCache` dispatch stamp** — `updateMany({ userId: "<id>" }, { $unset: { userId, requestId } })`. The row itself stays (shared); only the dispatching user's id and request id are unset. The id is a **string** here, not an ObjectId — `POST /summarize` receives it as JSON. Matched row count is logged as `videoCacheRowsUnstamped` (not part of the audit `counts`).
8. **Qdrant user content** (placeholder — see below)
9. **S3 user objects** (placeholder — see below)
10. **`users`** (the FK anchor, dropped last)
11. **`userDeletions`** audit insert

### Idempotency

Every step is naturally idempotent: `deleteMany({ userId: oid })` (or, for the `videoSummaryCache` stamp, an `$unset` keyed on the user's id) produces the same result whether it runs once or fifty times. If the cascade fails partway, re-invoking it is safe — the first delete-many is a no-op (nothing matches), the second processes the remainder.

### Error handling

Each step is wrapped in `runStep`, which catches and records the error as a `warning` on the audit row **without aborting**. The cascade always reaches the `users` row deletion. The rationale: leaving the smallest possible PII footprint matters more than rolling back when one downstream service is misbehaving. The audit row preserves the failure for follow-up.

### What's NOT deleted (and why)

| Store | Why we don't delete |
|---|---|
| `videoSummaryCache` | Shared cache, keyed by YouTube ID, not user. Deletion would break other users with the same video. Removed only by the video cascade (`DELETE /api/admin/videos/:youtubeId`). **Exception — the dispatch stamp:** `POST /summarize` writes the dispatching user's `userId` and `requestId` onto the row (so the SSE producer can attribute `llm_usage`), overwritten on every dispatch and left in place after the run (vie-api adds the ledger's `billingRunId` beside them); cascade step 7 unsets all three for the deleted user. |
| Qdrant `transcript_chunks` | Same — video-scoped, not user-scoped. `user_id` payload is reserved but always `null` today (see `services/summarizer/src/services/vector/qdrant_service.py:153`). When that payload starts being populated for user-generated content, the cascade step is ready to flip from no-op to real delete. The video cascade deletes them by `video_id` through the summarizer's `POST /internal/videos/{id}/purge`. |
| S3 `videos/{youtubeId}/...` | Same — video-scoped. No user-keyed S3 prefixes today; placeholder hook is wired for the future. The video cascade deletes the whole prefix. |
| `llm_usage` | The cost ledger. Rows are kept indefinitely — there is **no TTL** (the legacy 90-day index is dropped at boot, see [`llm-cost-model.md`](./llm-cost-model.md#ledger-retention-decided-2026-07-06)). Rows carry the string `user_id` of whoever triggered the call, and the cascade has no `llm_usage` step, so after erasure the rows keep that now-orphaned id until they are 90 days old. Deleting them would erase billing history and distort cost reports. **After 90 days the billing repair job unsets `user_id` and `request_id`** on every row (`PRIVACY.md`: usage analytics older than 90 days carry no personal data; `request_id` would still join to the ledger's `source.ref`, whose rows name the user); cost, model and timestamp stay. |
| `shareLikes` / `shareViews` | Already anonymous (IP hashed). |
| Paddle records | Out of our system — directed at Paddle's own GDPR portal in [`PRIVACY.md`](./PRIVACY.md). |

## Soft-delete enforcement

A previously-issued JWT remains cryptographically valid for its 15-minute TTL even after the user is soft-deleted. To kill those sessions immediately, the `authenticate` hook in `api/src/plugins/jwt.ts` looks up the user on every request and returns **403 `ACCOUNT_DELETION_PENDING`** if `deletedAt` is set. The refresh-token cookie is also cleared on the `DELETE /api/users/me` response so the browser doesn't try to refresh into a frozen session.

`/api/users/me/restore` is the only authenticated endpoint that **does not** apply this check — by design, otherwise the user can never cancel.

## Running the sweep

**Scheduled (billing-roles P1.6).** vie-api's billing repair job runs `userDeletionService.runScheduledDeletions()` every `BILLING_REPAIR_INTERVAL_SECONDS` (default 300) under the Redis lock `vie:api:billing-repair`, so a due account is hard-deleted within one interval of its `hardDeleteAt`. The job is on when `BILLING_REPAIR_ENABLED` is (dev default on; the prod compose defaults it off until Mongo runs as a replica set, P6.4 — until then prod still needs the route below to be driven). An admin's soft delete keeps `initiatedBy: 'admin'` when the job runs it; a self-service one is recorded as `'scheduler'`.

The internal route stays for a manual or external run:

```bash
curl -X POST -H "x-internal-secret: $INTERNAL_SECRET" \
  http://vie-api:3000/internal/run-deletions
```

Driving it from an outside cron as well is harmless: every step is idempotent and a no-op past the first run.

Example k8s CronJob skeleton (illustrative only; nothing in this repo deploys it):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: vie-gdpr-cascade
spec:
  schedule: "0 3 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: trigger
            image: curlimages/curl
            args:
              - -X
              - POST
              - -H
              - "x-internal-secret: $(INTERNAL_SECRET)"
              - http://vie-api:3000/internal/run-deletions
          restartPolicy: OnFailure
```

## Audit log

`userDeletions` rows are **retained indefinitely** with no TTL. Each row holds:

- `originalUserId` (string form of the deleted ObjectId)
- `emailHash` — SHA-256 of the lowercased, trimmed email. Never the plaintext.
- `initiatedBy`: `'self' | 'admin' | 'scheduler'`. An admin-requested soft delete keeps `'admin'` when the sweep later runs it: the request stores `users.deletionOrigin` (`initiatedBy`, `adminId`, `reason`; an operator id, no PII), the sweep reads it, and a restore (`cancelDeletion`) clears it. A soft delete the user requested themselves has no origin and is recorded as `'scheduler'`.
- `adminId`, `reason` — populated when `initiatedBy === 'admin'`
- `counts` — rows deleted per inventory bucket
- `startedAt`, `completedAt`, `durationMs`
- `warnings[]` — per-step error messages when the cascade didn't run cleanly

Admin deletions also write an **`adminAudit`** row (see [`SECURITY.md`](./SECURITY.md#roles-and-permissions)), at request time for both modes: action `users.delete`, `targetType: 'user'`, `targetId` the user id, `after.mode` `'immediate'` or `'scheduled'`, the admin (`actorId`, the user id from the signed `x-admin-actor` header), the `reason`, request id and client ip. So the soft delete is attributed when it is requested, 30 days before the `userDeletions` row exists. The `adminAudit` row carries the user id but no email.

To prove a specific email was erased: hash the email with the same SHA-256 (lowercase, trim) and look it up in `userDeletions.emailHash`. The match plus the row's `completedAt` is the legal proof.

## Adding a new user-scoped data store

When you add a collection / payload / object prefix that ties to a user:

1. Add a step to `executeHardDelete()` in `user-deletion.service.ts`.
2. Add the corresponding count field to `DeletionCounts` in `user-deletion.repository.ts`.
3. Update [`reports/user-data-inventory.md`](../reports/user-data-inventory.md) under either the MongoDB / Qdrant / S3 table.
4. Add an assertion to the integration test in `api/src/services/__tests__/gdpr-cascade.integration.test.ts` proving the new data is removed.

Compliance is only as good as the inventory. Anything that touches user PII without showing up in the cascade is a future incident.

## Testing

```bash
# Unit + saga tests
cd api && npx vitest run src/services/__tests__/user-deletion.service.test.ts

# Repository tests
cd api && npx vitest run src/repositories/__tests__/user.repository.test.ts

# Route tests
cd api && npx vitest run src/routes/users.routes.test.ts

# Full end-to-end compliance gate
cd api && npx vitest run src/services/__tests__/gdpr-cascade.integration.test.ts
```

The integration test seeds every collection in the inventory, runs the cascade, and asserts zero rows + audit-log row exists. If it fails, deletion no longer satisfies Article 17 — **do not merge** until it passes again.
