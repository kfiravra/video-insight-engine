import type { FastifyBaseLogger } from 'fastify';
import type { Role, RoleSource } from '@vie/types';

import type { UserRepository, UserRoleSummary } from '../repositories/user.repository.js';
import type { AuditService } from './audit.service.js';

/** Audit actor for every change this service makes. */
export const SEED_ACTOR_ID = 'system:boot';
const SEED_REASON = 'ADMIN_EMAILS reconcile at boot';

export type AdminSeedStatus = 'skipped_empty' | 'refused_zero_admins' | 'reconciled';

export interface AdminSeedResult {
  status: AdminSeedStatus;
  promoted: number;
  demoted: number;
}

/** What a reconcile would change, computed before anything is written. */
export interface AdminSeedPlan {
  promotions: UserRoleSummary[];
  demotions: UserRoleSummary[];
  /** Listed emails with no matching account (never created from env). */
  unknownEmails: string[];
  /** Listed accounts that exist but are soft-deleted or the shared demo account. */
  ineligible: UserRoleSummary[];
}

/**
 * Normalise the raw `ADMIN_EMAILS` value: split on commas (and stray
 * whitespace or semicolons), trim, lowercase, drop blanks and duplicates.
 */
export function parseAdminEmails(raw: string): string[] {
  const emails = raw
    .split(/[\s,;]+/)
    .map((email) => email.trim().toLowerCase())
    .filter((email) => email.length > 0);
  return [...new Set(emails)];
}

/**
 * Decide what the reconcile changes, without writing. Pure so the rules are
 * testable on their own:
 *  - a listed existing, active, non-demo account without the admin role is promoted;
 *  - an admin whose `roleSource` is `env` and whose email left the list is demoted;
 *  - `granted` admins (and legacy admins, which read as `granted`) are never touched.
 */
export function planAdminSeed(
  emails: string[],
  listedUsers: UserRoleSummary[],
  admins: UserRoleSummary[],
  demoEmail: string,
): AdminSeedPlan {
  const listed = new Set(emails);
  const byEmail = new Map(listedUsers.map((user) => [user.email.toLowerCase(), user]));

  const promotions: UserRoleSummary[] = [];
  const ineligible: UserRoleSummary[] = [];
  for (const email of emails) {
    const user = byEmail.get(email);
    if (!user || user.roles.includes('admin')) continue;
    if (user.deletedAt || email === demoEmail) ineligible.push(user);
    else promotions.push(user);
  }

  return {
    promotions,
    ineligible,
    unknownEmails: emails.filter((email) => !byEmail.has(email)),
    demotions: admins.filter(
      (admin) => admin.roleSource === 'env' && !listed.has(admin.email.toLowerCase()),
    ),
  };
}

/** Active (not soft-deleted) admins that remain once the plan is applied. */
function adminsAfter(plan: AdminSeedPlan, admins: UserRoleSummary[]): number {
  const demotedIds = new Set(plan.demotions.map((admin) => admin.id));
  const staying = admins.filter((admin) => !admin.deletedAt && !demotedIds.has(admin.id));
  return staying.length + plan.promotions.length;
}

/** `k***@gmail.com`: enough for an operator to spot a typo, never the full address. */
function maskEmail(email: string): string {
  const at = email.indexOf('@');
  return at <= 0 ? '***' : `${email[0]}***${email.slice(at)}`;
}

/**
 * Reconciles `ADMIN_EMAILS` with the `users` collection at vie-api boot:
 * promotes listed existing accounts to `roles: ['admin']` with
 * `roleSource: 'env'`, demotes `env` admins whose email was removed, and
 * writes one `adminAudit` row (actor `system:boot`) per change. It never
 * creates users, never touches `granted` admins, and refuses (changing
 * nothing) when the result would leave no active admin. An empty list is a
 * no-op, so a missing env row can never lock anyone out. Idempotent: a second
 * run with the same inputs writes nothing.
 */
export class AdminSeedService {
  constructor(
    private readonly adminEmails: string,
    private readonly demoEmail: string,
    private readonly userRepository: Pick<
      UserRepository,
      'listAdmins' | 'findRoleSummariesByEmails' | 'setRoles'
    >,
    private readonly auditService: Pick<AuditService, 'recordBestEffort'>,
    private readonly logger: FastifyBaseLogger,
  ) {}

  async run(): Promise<AdminSeedResult> {
    const emails = parseAdminEmails(this.adminEmails);
    if (emails.length === 0) {
      this.logger.warn(
        { hint: 'set ADMIN_EMAILS to the admin login emails; no admin role was changed' },
        'admin_seed_skipped_empty',
      );
      return { status: 'skipped_empty', promoted: 0, demoted: 0 };
    }

    const [listedUsers, admins] = await Promise.all([
      this.userRepository.findRoleSummariesByEmails(emails),
      this.userRepository.listAdmins(),
    ]);
    const plan = planAdminSeed(emails, listedUsers, admins, this.demoEmail.trim().toLowerCase());
    this.logPlanWarnings(plan);

    const remaining = adminsAfter(plan, admins);
    if (remaining === 0) {
      return this.handleNoAdmins(plan);
    }

    const promoted = await this.applyPromotions(plan.promotions);
    const demoted = await this.applyDemotions(plan, promoted);
    this.logger.info({ promoted, demoted, admins: remaining }, 'admin_seed_reconciled');
    return { status: 'reconciled', promoted, demoted };
  }

  /**
   * The plan leaves no active admin. With demotions planned that is a refusal
   * (nothing is written); with none planned there is simply no admin yet.
   */
  private handleNoAdmins(plan: AdminSeedPlan): AdminSeedResult {
    if (plan.demotions.length > 0) {
      this.logger.error(
        { demotions: plan.demotions.length },
        'admin_seed_refused_zero_admins',
      );
      return { status: 'refused_zero_admins', promoted: 0, demoted: 0 };
    }
    this.logger.warn('admin_seed_no_active_admin');
    return { status: 'reconciled', promoted: 0, demoted: 0 };
  }

  private logPlanWarnings(plan: AdminSeedPlan): void {
    for (const email of plan.unknownEmails) {
      this.logger.warn({ email: maskEmail(email) }, 'admin_seed_unknown_email');
    }
    for (const user of plan.ineligible) {
      this.logger.warn({ userId: user.id }, 'admin_seed_ineligible_user');
    }
  }

  private async applyPromotions(users: UserRoleSummary[]): Promise<number> {
    let promoted = 0;
    for (const user of users) {
      const changed = await this.change(user, ['admin'], 'env', 'roles.seed.promote');
      if (changed) promoted++;
    }
    return promoted;
  }

  /** Demotions wait for every promotion to land, so a failed promotion can never leave zero admins. */
  private async applyDemotions(plan: AdminSeedPlan, promoted: number): Promise<number> {
    if (promoted < plan.promotions.length && plan.demotions.length > 0) {
      this.logger.error(
        { promotionsFailed: plan.promotions.length - promoted },
        'admin_seed_demotions_skipped',
      );
      return 0;
    }
    let demoted = 0;
    for (const user of plan.demotions) {
      const changed = await this.change(user, [], 'env', 'roles.seed.demote');
      if (changed) demoted++;
    }
    return demoted;
  }

  /** One role change plus its audit row; a failure is logged and reported as not changed. */
  private async change(
    user: UserRoleSummary,
    roles: Role[],
    roleSource: RoleSource,
    action: 'roles.seed.promote' | 'roles.seed.demote',
  ): Promise<boolean> {
    try {
      const matched = await this.userRepository.setRoles(user.id, roles, roleSource);
      if (!matched) {
        this.logger.warn({ userId: user.id, action }, 'admin_seed_user_vanished');
        return false;
      }
    } catch (err) {
      this.logger.error({ err, userId: user.id, action }, 'admin_seed_change_failed');
      return false;
    }
    await this.auditService.recordBestEffort({
      actorId: SEED_ACTOR_ID,
      action,
      targetType: 'role',
      targetId: user.id,
      before: { roles: user.roles, roleSource: user.roleSource },
      after: { roles, roleSource: roles.length > 0 ? roleSource : null },
      reason: SEED_REASON,
    });
    return true;
  }
}
