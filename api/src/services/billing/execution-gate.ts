import { randomUUID } from 'node:crypto';
import { AppError, ServiceUnavailableError } from '../../utils/errors.js';
import type { BillingService } from './billing.service.js';

/**
 * The token gate of a chat turn, library chat or action, run by the route
 * before anything is proxied: blocked → 403 ACCOUNT_BLOCKED, nothing to spend
 * → 402 INSUFFICIENT_TOKENS. Fails closed — a ledger that cannot be read is
 * 503, never a free turn. Returns the execution id the assistant reports the
 * turn's cost under (`X-Execution-Id` → POST /internal/assistant/usage).
 */
export async function admitExecution(billing: Pick<BillingService, 'admitExecution'>, userId: string): Promise<string> {
  try {
    await billing.admitExecution(userId);
  } catch (err) {
    if (err instanceof AppError) throw err;
    throw new ServiceUnavailableError('Billing');
  }
  return randomUUID();
}
