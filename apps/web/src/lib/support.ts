// The team's contact address, for the notes that say tokens are added by the
// team while payments are a placeholder. Baked in at build time from
// VITE_SUPPORT_EMAIL; read on each call so tests can stub it.

/** One address, no spaces or a second `@`: anything else would make a broken mailto. */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** VITE_SUPPORT_EMAIL when it holds one address; null when unset, empty or malformed. */
export function supportEmail(): string | null {
  const value = (import.meta.env.VITE_SUPPORT_EMAIL as string | undefined)?.trim() ?? "";
  return EMAIL_PATTERN.test(value) ? value : null;
}

/** `mailto:` for `email`, opening with `subject`. */
export function supportMailto(email: string, subject: string): string {
  return `mailto:${email}?subject=${encodeURIComponent(subject)}`;
}
