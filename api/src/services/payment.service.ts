import { createHmac, timingSafeEqual } from 'node:crypto';
import { ObjectId } from 'mongodb';
import { FastifyBaseLogger } from 'fastify';
import { PaymentError } from '../utils/errors.js';
import type { UserTier, TierLimits } from '@vie/types';
import { config } from '../config.js';
import { UserRepository } from '../repositories/user.repository.js';

interface PaddleEvent {
  event_type: string;
  data: Record<string, unknown>;
}

const TIER_LIMITS_MAP: Record<UserTier, TierLimits> = {
  free: { videosPerDay: 3, chatPerOutput: 5, shareEnabled: true, exportEnabled: false },
  pro: { videosPerDay: -1, chatPerOutput: -1, shareEnabled: true, exportEnabled: true },
  team: { videosPerDay: -1, chatPerOutput: -1, shareEnabled: true, exportEnabled: true },
};

// Paddle signs `${ts}:${rawBody}` where `ts` is a unix-seconds timestamp.
// Rejecting stale timestamps bounds the replay window of a captured webhook
// to 5 minutes (Paddle's own recommended tolerance).
const WEBHOOK_MAX_AGE_SECONDS = 300;

export class PaymentService {
  constructor(
    private readonly userRepository: UserRepository,
    private readonly logger: FastifyBaseLogger
  ) {}

  /** Verify Paddle webhook signature (Paddle Billing format: ts=<timestamp>;h1=<hash>) */
  verifyWebhook(rawBody: string, signature: string): boolean {
    if (!config.PADDLE_WEBHOOK_SECRET) return false;

    try {
      // Parse Paddle's signature format: ts=1234567890;h1=<hex_hash>
      const parts = Object.fromEntries(
        signature.split(';').map(p => {
          const idx = p.indexOf('=');
          return idx > 0 ? [p.slice(0, idx), p.slice(idx + 1)] : [p, ''];
        })
      );
      const ts = parts.ts;
      const h1 = parts.h1;
      if (!ts || !h1) return false;

      // Freshness check — a valid-but-old signature is a replay, not a webhook.
      const tsSeconds = Number(ts);
      if (!Number.isFinite(tsSeconds)) return false;
      const ageSeconds = Math.floor(Date.now() / 1000) - tsSeconds;
      if (ageSeconds > WEBHOOK_MAX_AGE_SECONDS) return false;

      const hmac = createHmac('sha256', config.PADDLE_WEBHOOK_SECRET);
      hmac.update(`${ts}:${rawBody}`);
      const expected = hmac.digest('hex');
      return timingSafeEqual(Buffer.from(expected), Buffer.from(h1));
    } catch {
      return false;
    }
  }

  /** Handle incoming webhook event from Paddle */
  async handleWebhookEvent(event: PaddleEvent): Promise<void> {
    const { event_type, data } = event;
    const customerId = data.customer_id as string | undefined;
    const subscriptionId = data.id as string | undefined;

    this.logger.info({ event_type, customerId }, 'Processing Paddle webhook');

    if (!customerId) {
      this.logger.warn({ event_type }, 'Webhook missing customer_id');
      return;
    }

    // Find user by Paddle customer ID
    const user = await this.userRepository.findByPaddleCustomerId(customerId);
    if (!user) {
      this.logger.warn({ customerId }, 'No user found for Paddle customer');
      return;
    }

    switch (event_type) {
      case 'subscription.created':
      case 'subscription.activated':
        await this.setUserTier(user._id, 'pro', subscriptionId);
        break;

      case 'subscription.updated': {
        const status = data.status as string;
        if (status === 'active') {
          await this.setUserTier(user._id, 'pro', subscriptionId);
        }
        break;
      }

      case 'subscription.canceled':
      case 'subscription.cancelled':
        await this.setUserTier(user._id, 'free');
        break;

      case 'subscription.past_due':
        this.logger.warn({ userId: user._id.toString() }, 'Subscription past due — grace period');
        break;

      default:
        this.logger.debug({ event_type }, 'Unhandled webhook event');
    }
  }

  /** Generate a Paddle checkout URL */
  async generateCheckoutUrl(userId: string, tier: 'pro' | 'team'): Promise<string> {
    const user = await this.userRepository.findById(userId);
    if (!user) {
      throw new PaymentError('User not found');
    }

    const priceId = tier === 'pro'
      ? config.PADDLE_PRO_PRICE_ID
      : config.PADDLE_TEAM_PRICE_ID;

    if (!priceId) {
      throw new PaymentError(`No price configured for tier: ${tier}`);
    }

    // Build Paddle checkout URL
    const params = new URLSearchParams({
      items: JSON.stringify([{ priceId, quantity: 1 }]),
      customer_email: user.email,
      passthrough: JSON.stringify({ userId }),
    });

    return `https://checkout.paddle.com/checkout/custom?${params}`;
  }

  /** Get user tier and limits */
  async getUserTier(userId: string): Promise<{ tier: UserTier; limits: TierLimits }> {
    const tierValue = await this.userRepository.getTier(userId);
    const tier: UserTier = tierValue === 'pro' || tierValue === 'team' ? tierValue : 'free';
    return { tier, limits: TIER_LIMITS_MAP[tier] };
  }

  private async setUserTier(userId: ObjectId, tier: UserTier, subscriptionId?: string): Promise<void> {
    const userIdStr = userId.toString();

    const update: Partial<{ tier: string; tierUpdatedAt: Date; paddleSubscriptionId: string }> = {
      tier,
      tierUpdatedAt: new Date(),
    };
    if (subscriptionId) {
      update.paddleSubscriptionId = subscriptionId;
    }

    // Atomic read+write: get previous state and apply update in one operation
    const previousUser = await this.userRepository.updateAndReturnPrevious(userIdStr, update);
    const previousTier: UserTier = previousUser?.tier === 'pro' || previousUser?.tier === 'team'
      ? previousUser.tier
      : 'free';

    this.logger.info({ userId: userIdStr, tier, previousTier }, 'User tier updated');
  }
}
