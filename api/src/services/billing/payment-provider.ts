import type { PaymentEvent } from '@vie/types';
import { ProviderNotConfiguredError } from '../../utils/errors.js';

/**
 * What a checkout buys: the Pro subscription, or the credit pack. v1 sells one
 * pack (`CREDIT_PACKS`, Q4); a second pack widens `pack` to carry a pack id.
 */
export type CheckoutKind = 'subscription' | 'pack';

/** A provider-hosted page the web redirects to. */
export interface ProviderUrl {
  url: string;
}

/**
 * Everything the billing code needs from a payment provider, so the provider
 * stays swappable. P4 adds the real adapter; until then `NoopPaymentProvider`
 * is the only implementation and nothing calls it.
 */
export interface PaymentProviderAdapter {
  /** Short provider name stamped on every parsed event. */
  readonly name: string;
  /** Checkout page for `accountId`; the account id travels in the provider metadata. */
  createCheckout(kind: CheckoutKind, accountId: string): Promise<ProviderUrl>;
  /** The provider's customer portal (invoices, cancel) for `accountId`. */
  createPortal(accountId: string): Promise<ProviderUrl>;
  /** True only when `signature` is a fresh, valid signature of the exact `rawBody` bytes. */
  verifyWebhook(rawBody: string, signature: string): boolean;
  /** Normalise a verified webhook body. Throws on a body that is not a provider event. */
  parseEvent(rawBody: string): PaymentEvent;
}

/**
 * The adapter while no provider is configured. Every method throws
 * ProviderNotConfiguredError, `verifyWebhook` included: answering `false`
 * would make a future webhook route report a bad signature instead of "no
 * provider", and answering `true` would accept unsigned events.
 */
export class NoopPaymentProvider implements PaymentProviderAdapter {
  readonly name = 'none';

  async createCheckout(_kind: CheckoutKind, _accountId: string): Promise<ProviderUrl> {
    throw new ProviderNotConfiguredError();
  }

  async createPortal(_accountId: string): Promise<ProviderUrl> {
    throw new ProviderNotConfiguredError();
  }

  verifyWebhook(_rawBody: string, _signature: string): boolean {
    throw new ProviderNotConfiguredError();
  }

  parseEvent(_rawBody: string): PaymentEvent {
    throw new ProviderNotConfiguredError();
  }
}
