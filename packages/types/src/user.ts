// ═══════════════════════════════════════════════════
// User & Tier Types
// ═══════════════════════════════════════════════════

/** User theme preference for persistence across devices */
export type ThemePreference = 'system' | 'light' | 'dark';

/**
 * User record stored in MongoDB `users` collection.
 * Optional fields (?) are safe for pre-v1.4 documents — mark required after backfill confirmed.
 * @migration Mark `tier` required after backfill-v1.4.ts confirmed on production.
 */
export interface User {
  _id: string;
  email: string;
  name: string;
  /** Unique public handle. Sparse unique index — null until user sets one. */
  username?: string | null;
  /** Display name for public profiles. Falls back to `name` if absent. */
  displayName?: string | null;
  /** @migration Mark required after backfill-v1.4.ts confirmed */
  tier?: UserTier;
  /** Persisted theme preference. Synced from frontend via PATCH /users/me/preferences. */
  themePreference?: ThemePreference;
  /** Share slug that referred this user during signup */
  referralSlug?: string | null;
  createdAt: string;  // ISO date
  updatedAt: string;  // ISO date
}

/**
 * Like record stored in MongoDB `likes` collection.
 * Compound unique index: `{ outputId, ipHash }` — prevents duplicate likes.
 */
export interface Like {
  _id: string;
  outputId: string;
  userId?: string | null;
  /** SHA-256 hash of IP for dedup without storing raw IPs */
  ipHash: string;
  createdAt: string;  // ISO date
}

/** User subscription tier */
export type UserTier = 'free' | 'pro' | 'team';
