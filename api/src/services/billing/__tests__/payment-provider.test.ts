import { describe, it, expect } from 'vitest';
import { NoopPaymentProvider } from '../payment-provider.js';
import type { PaymentProviderAdapter } from '../payment-provider.js';
import { ProviderNotConfiguredError } from '../../../utils/errors.js';

const ACCOUNT_ID = '6a7b2f6abacdb32871507996';

describe('NoopPaymentProvider', () => {
  const provider: PaymentProviderAdapter = new NoopPaymentProvider();

  describe('every adapter method', () => {
    const calls: Array<[string, () => unknown]> = [
      ['createCheckout', () => provider.createCheckout('subscription', ACCOUNT_ID)],
      ['createPortal', () => provider.createPortal(ACCOUNT_ID)],
      ['verifyWebhook', () => provider.verifyWebhook('{}', 'ts=1;h1=abc')],
      ['parseEvent', () => provider.parseEvent('{}')],
    ];

    it.each(calls)('should reject %s with PROVIDER_NOT_CONFIGURED', async (_name, call) => {
      await expect(Promise.resolve().then(call)).rejects.toMatchObject({
        code: 'PROVIDER_NOT_CONFIGURED',
        status: 501,
      });
    });

    it.each(calls)('should reject %s with a ProviderNotConfiguredError', async (_name, call) => {
      await expect(Promise.resolve().then(call)).rejects.toBeInstanceOf(ProviderNotConfiguredError);
    });
  });

  it('should not answer false to verifyWebhook, which would pass for a bad signature', () => {
    expect(() => provider.verifyWebhook('{}', '')).toThrow(ProviderNotConfiguredError);
  });

  it('should reject a pack checkout the same way as a subscription checkout', async () => {
    await expect(provider.createCheckout('pack', ACCOUNT_ID)).rejects.toBeInstanceOf(
      ProviderNotConfiguredError,
    );
  });

  it('should carry a message that is safe to show a user', () => {
    expect(new ProviderNotConfiguredError().message).toBe('Payments are not available yet.');
  });
});
