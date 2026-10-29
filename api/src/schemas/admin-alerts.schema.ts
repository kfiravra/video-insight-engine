import { z } from 'zod';

/**
 * `POST /api/admin/alerts/config`: a full replacement of the three thresholds
 * with a required reason. Strict, so a stray field is a 400. The bounds are the
 * ones vie-admin's evaluator assumes (cost ≥ 0, spike multiplier ≥ 1, rate 0–1).
 */
export const alertConfigBodySchema = z
  .object({
    cost_threshold_usd: z.number().finite().min(0),
    daily_spike_multiplier: z.number().finite().min(1),
    failure_rate_threshold: z.number().finite().min(0).max(1),
    reason: z.string().trim().min(1, 'reason is required').max(500, 'reason is at most 500 characters'),
  })
  .strict();
