/**
 * Cached `Intl.DateTimeFormat` instances keyed by the resolved timezone.
 *
 * Constructing `Intl.DateTimeFormat` is relatively expensive, and
 * `formatDateTime` is called once per table row (up to ~200 rows). The
 * formatters are stateless aside from the timezone, so we build them lazily
 * and reuse them, rebuilding only if the resolved timezone changes.
 */
let _formatterCache: {
  tz: string;
  date: Intl.DateTimeFormat;
  time: Intl.DateTimeFormat;
  tzName: Intl.DateTimeFormat;
} | null = null;

function _formatters(): NonNullable<typeof _formatterCache> {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!_formatterCache || _formatterCache.tz !== tz) {
    _formatterCache = {
      tz,
      date: new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        timeZone: tz,
      }),
      time: new Intl.DateTimeFormat('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        timeZone: tz,
      }),
      tzName: new Intl.DateTimeFormat('en-US', {
        timeZone: tz,
        timeZoneName: 'short',
      }),
    };
  }
  return _formatterCache;
}

/**
 * Format an ISO timestamp as a local absolute date-time with an explicit
 * timezone label, e.g. "Jun 3, 2026, 1:04 PM PST".
 *
 * Uses `Intl.DateTimeFormat` so the label reflects the user's resolved
 * timezone (IANA short name when available, otherwise offset-based fallback).
 *
 * Returns "—" for null/undefined/invalid inputs.
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';

  const { date, time, tzName, tz } = _formatters();

  const datePart = date.format(d);
  const timePart = time.format(d);

  // Extract the short tz abbreviation (e.g. "PST", "UTC+2", "IST").
  const tzLabel =
    tzName.formatToParts(d).find((p) => p.type === 'timeZoneName')?.value ?? tz;

  return `${datePart}, ${timePart} ${tzLabel}`;
}

/** Format seconds into "mm:ss" or "h:mm:ss". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = String(m).padStart(h > 0 ? 2 : 1, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Relative time string from an ISO date. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return 'just now';
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

/** Format USD cost with 4 decimal places. */
export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return '$0.0000';
  return `$${usd.toFixed(4)}`;
}

/** Format large numbers with locale separators. */
export function formatNumber(n: number | null | undefined): string {
  if (n == null) return '0';
  return n.toLocaleString();
}

export interface UsageVolumeFields {
  unit?: string | null;
  audio_seconds?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
}

/**
 * The "Tokens" cell for an llm_usage row. Transcription rows are priced per
 * audio second and carry zero tokens — rendering them as "0" made every
 * Whisper call look free/broken.
 */
export function formatUsageVolume(row: UsageVolumeFields): string {
  if (row.unit === 'audio_seconds') {
    return `${((row.audio_seconds ?? 0) / 60).toFixed(1)} min audio`;
  }
  return formatNumber((row.tokens_in ?? 0) + (row.tokens_out ?? 0));
}

/** `64f1a2…9c0b`: a long id shortened for tables and headers; short ids are returned as is. */
export function shortId(id: string): string {
  return id.length > 10 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id;
}
