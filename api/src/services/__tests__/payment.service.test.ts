import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { getTestDb, mockLogger } from '../../test/setup.js';
import { PaymentService } from '../payment.service.js';
import { UserRepository } from '../../repositories/user.repository.js';
import { PaymentError } from '../../utils/errors.js';

// Mock config - must be before PaymentService import uses it
vi.mock('../../config.js', () => ({
  config: {
    PADDLE_WEBHOOK_SECRET: '',
    PADDLE_PRO_PRICE_ID: 'pri_pro_123',
    PADDLE_TEAM_PRICE_ID: 'pri_team_456',
  },
}));

// Import mocked config so we can mutate it in tests
import { config } from '../../config.js';

describe('PaymentService', () => {
  let paymentService: PaymentService;
  let userRepository: UserRepository;

  beforeEach(() => {
    vi.clearAllMocks();
    // Reset webhook secret to empty (default)
    (config as Record<string, unknown>).PADDLE_WEBHOOK_SECRET = '';

    const db = getTestDb();
    userRepository = new UserRepository(db);
    paymentService = new PaymentService(userRepository, mockLogger as unknown as FastifyBaseLogger);
  });

  describe('verifyWebhook', () => {
    it('should return false when no webhook secret is configured', () => {
      const result = paymentService.verifyWebhook('body', 'ts=123;h1=abc');

      expect(result).toBe(false);
    });

    it('should return false for malformed signature (no ts/h1)', () => {
      (config as Record<string, unknown>).PADDLE_WEBHOOK_SECRET = 'test-secret';
      const result = paymentService.verifyWebhook('body', 'bad-signature');

      expect(result).toBe(false);
    });

    it('should verify valid Paddle Billing signature format', () => {
      const secret = 'test-webhook-secret';
      (config as Record<string, unknown>).PADDLE_WEBHOOK_SECRET = secret;

      const { createHmac } = require('node:crypto');
      // ts must be fresh: verifyWebhook rejects signatures older than
      // WEBHOOK_MAX_AGE_SECONDS (replay protection, task 1.7)
      const ts = String(Math.floor(Date.now() / 1000));
      const rawBody = '{"event_type":"subscription.created","data":{}}';
      const hmac = createHmac('sha256', secret);
      hmac.update(`${ts}:${rawBody}`);
      const h1 = hmac.digest('hex');

      const signature = `ts=${ts};h1=${h1}`;
      const result = paymentService.verifyWebhook(rawBody, signature);

      expect(result).toBe(true);
    });

    it('should reject invalid signature hash', () => {
      (config as Record<string, unknown>).PADDLE_WEBHOOK_SECRET = 'test-secret';

      const result = paymentService.verifyWebhook('body', 'ts=123;h1=invalid_hash');

      expect(result).toBe(false);
    });
  });

  describe('getUserTier', () => {
    it('should return free tier for users without tier set', async () => {
      const db = getTestDb();
      const userId = new ObjectId();
      await db.collection('users').insertOne({
        _id: userId,
        email: 'test@example.com',
      });

      const result = await paymentService.getUserTier(userId.toString());

      expect(result).toEqual({
        tier: 'free',
        limits: {
          videosPerDay: 3,
          chatPerOutput: 5,
          shareEnabled: true,
          exportEnabled: false,
        },
      });
    });

    it('should return correct tier and limits for pro user', async () => {
      const db = getTestDb();
      const userId = new ObjectId();
      await db.collection('users').insertOne({
        _id: userId,
        email: 'pro@example.com',
        tier: 'pro',
      });

      const result = await paymentService.getUserTier(userId.toString());

      expect(result).toEqual({
        tier: 'pro',
        limits: {
          videosPerDay: -1,
          chatPerOutput: -1,
          shareEnabled: true,
          exportEnabled: true,
        },
      });
    });
  });

  describe('handleWebhookEvent', () => {
    it('should set tier to pro on subscription.created', async () => {
      const db = getTestDb();
      const userId = new ObjectId();
      const customerId = 'ctm_abc123';
      const subscriptionId = 'sub_xyz789';

      await db.collection('users').insertOne({
        _id: userId,
        email: 'user@example.com',
        paddleCustomerId: customerId,
        tier: 'free',
      });

      await paymentService.handleWebhookEvent({
        event_type: 'subscription.created',
        data: { customer_id: customerId, id: subscriptionId },
      });

      const updatedUser = await db.collection('users').findOne({ _id: userId });
      expect(updatedUser?.tier).toBe('pro');
      expect(updatedUser?.paddleSubscriptionId).toBe(subscriptionId);
      expect(updatedUser?.tierUpdatedAt).toBeInstanceOf(Date);
    });

    it('should set tier to free on subscription.cancelled', async () => {
      const db = getTestDb();
      const userId = new ObjectId();
      const customerId = 'ctm_cancel123';

      await db.collection('users').insertOne({
        _id: userId,
        email: 'cancel@example.com',
        paddleCustomerId: customerId,
        tier: 'pro',
        paddleSubscriptionId: 'sub_old',
      });

      await paymentService.handleWebhookEvent({
        event_type: 'subscription.cancelled',
        data: { customer_id: customerId, id: 'sub_old' },
      });

      const updatedUser = await db.collection('users').findOne({ _id: userId });
      expect(updatedUser?.tier).toBe('free');
    });

    it('should ignore events without customer_id', async () => {
      await paymentService.handleWebhookEvent({
        event_type: 'subscription.created',
        data: { id: 'sub_no_customer' },
      });

      expect(mockLogger.warn).toHaveBeenCalledWith(
        { event_type: 'subscription.created' },
        'Webhook missing customer_id',
      );
    });
  });

  describe('generateCheckoutUrl', () => {
    it('should throw PaymentError if user not found', async () => {
      const nonExistentId = new ObjectId().toString();

      await expect(
        paymentService.generateCheckoutUrl(nonExistentId, 'pro'),
      ).rejects.toThrow(PaymentError);

      await expect(
        paymentService.generateCheckoutUrl(nonExistentId, 'pro'),
      ).rejects.toThrow('User not found');
    });
  });
});
