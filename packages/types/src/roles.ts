// ═══════════════════════════════════════════════════
// Roles, permissions & the admin audit trail
// ═══════════════════════════════════════════════════

/**
 * Every action an admin-gated route can require. Routes name the permission
 * they need (`fastify.requireAdmin('plan:set')`), never a role, so a future
 * `support` role is a new `ROLE_PERMISSIONS` row and no route changes.
 */
export const PERMISSIONS = [
  'billing:read',
  'billing:grant',
  'plan:set',
  'caps:set',
  'users:delete',
  'videos:purge',
  'queue:read',
  'queue:replay',
  'alerts:write',
  'roles:manage',
  'audit:read',
  // Cold-media benchmark runs (`cold` on POST /api/videos) — the admin half of
  // `UserRepository.mayRunCold`; the eval account is the other half.
  'cold:run',
] as const;

export type Permission = (typeof PERMISSIONS)[number];

/**
 * The only permissions the shared machine key (`ADMIN_API_KEY`, no actor) may
 * use: reads for health checks and dashboards. Every mutation needs a named
 * human actor.
 */
export const READ_PERMISSIONS: ReadonlySet<Permission> = new Set<Permission>([
  'billing:read',
  'audit:read',
  'queue:read',
]);

/** Roles stored on `users.roles`. `'support'` (read-only admin) is reserved for a later task (Q14). */
export const ROLES = ['admin'] as const;

export type Role = (typeof ROLES)[number];

export const ROLE_PERMISSIONS: Readonly<Record<Role, readonly Permission[]>> = {
  admin: PERMISSIONS,
};

/**
 * Where a role came from. `env` roles are reconciled with `ADMIN_EMAILS` at
 * every vie-api boot (and removed when the email leaves the list); `granted`
 * roles are set by an admin or a migration and boot seeding never touches them.
 */
export type RoleSource = 'env' | 'granted';

/** True when `value` is a role this build knows; guards untrusted DB values. */
export function isRole(value: unknown): value is Role {
  return typeof value === 'string' && (ROLES as readonly string[]).includes(value);
}

/** True when any of `roles` grants `permission`. Unknown roles grant nothing. */
export function rolesGrant(roles: readonly string[], permission: Permission): boolean {
  return roles.some((role) => isRole(role) && ROLE_PERMISSIONS[role].includes(permission));
}

// ───────────────────────────────────────────────────
// Admin audit
// ───────────────────────────────────────────────────

/** What kind of thing an admin mutation changed. `role` rows use the affected user's id as `targetId`. */
export type AdminAuditTargetType = 'user' | 'video' | 'queue' | 'alert' | 'role';

/**
 * One row of the `adminAudit` collection: who did what to which thing.
 * Written by `AuditService.record` for every admin mutation; never updated,
 * never deleted. `before` / `after` hold small plain-JSON snapshots and must
 * never carry secrets or credentials.
 */
export interface AdminAudit {
  /** ObjectId hex. */
  _id: string;
  /** `users._id` hex of the acting admin, or `system:<name>` (`system:boot`, `system:admin-key`). */
  actorId: string;
  /** Stored only in the row; never written to logs. */
  actorEmail?: string;
  /** Dotted verb, e.g. `users.delete`, `videos.purge`, `queue.replay`, `roles.seed.promote`. */
  action: string;
  targetType: AdminAuditTargetType;
  /** The affected user id, youtube id, queue name, … */
  targetId: string;
  before?: unknown;
  after?: unknown;
  reason?: string;
  requestId?: string;
  ip?: string;
  /**
   * sha256 of the request that produced the row (action, target, parameters,
   * reason). A repeated `Idempotency-Key` with a different request is refused
   * by comparing against it. Internal: not part of the view.
   */
  requestHash?: string;
  /** ISO date. */
  createdAt: string;
}

/** `AdminAudit` as the admin API returns it: `_id` is exposed as `id`, `requestHash` is withheld. */
export interface AdminAuditView extends Omit<AdminAudit, '_id' | 'requestHash'> {
  id: string;
}
