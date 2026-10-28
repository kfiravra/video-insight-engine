import type { DurationLabels, PluralForms } from "@/lib/i18n/labels";

// Number, plural and relative-time formatting for the minutes UI. Plural
// categories come from Intl.PluralRules, so Hebrew (one / two / other) and
// Arabic (zero / one / two / few / many / other) get their own noun forms
// instead of English's singular-or-plural split. Digits stay Latin in every
// language, like the rest of the product (timestamps, scores).

const MS_PER_MINUTE = 60_000;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;

/** Locale tag with Latin digits, e.g. `ar` -> `ar-u-nu-latn`. */
function latinLocale(language: string): string {
  return `${language}-u-nu-latn`;
}

/** `1,250` in the language's grouping style. */
export function formatNumber(count: number, language: string): string {
  return new Intl.NumberFormat(latinLocale(language)).format(count);
}

/**
 * The noun phrase for `count` in the form its language needs. `{count}` in the
 * form is replaced by the formatted number; forms without it ("דקה אחת") are
 * returned as written. A category the table does not fill falls back to `other`.
 */
export function formatPlural(count: number, language: string, forms: PluralForms): string {
  const category = new Intl.PluralRules(language).select(count);
  const form = forms[category] ?? forms.other;
  return form.replace("{count}", formatNumber(count, language));
}

/** "1 minute" / "150 minutes" / Hebrew "שתי דקות" / Arabic "دقيقتان". */
export function formatMinutes(count: number, language: string, forms: PluralForms): string {
  return formatPlural(count, language, forms);
}

/** Replace `{name}` placeholders; a placeholder with no value is left as written. */
export function fillTemplate(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  );
}

function unitText(template: string, count: number, language: string): string {
  return template.replace("{count}", formatNumber(count, language));
}

/**
 * How long until `iso`, in the two largest units that apply: "7h 30m", "2d 3h",
 * "12m". Under a minute reads as "1m". Returns null for an unparseable
 * timestamp and for one already past, so callers can show "shortly" copy.
 */
export function formatDurationUntil(
  iso: string,
  nowMs: number,
  language: string,
  units: DurationLabels,
): string | null {
  const target = new Date(iso).getTime();
  if (Number.isNaN(target) || target <= nowMs) return null;

  const totalMinutes = Math.max(Math.floor((target - nowMs) / MS_PER_MINUTE), 1);
  const days = Math.floor(totalMinutes / (MINUTES_PER_HOUR * HOURS_PER_DAY));
  const hours = Math.floor(totalMinutes / MINUTES_PER_HOUR) % HOURS_PER_DAY;
  const minutes = totalMinutes % MINUTES_PER_HOUR;

  const parts: string[] = [];
  if (days > 0) {
    parts.push(unitText(units.days, days, language));
    if (hours > 0) parts.push(unitText(units.hours, hours, language));
  } else if (hours > 0) {
    parts.push(unitText(units.hours, hours, language));
    if (minutes > 0) parts.push(unitText(units.minutes, minutes, language));
  } else {
    parts.push(unitText(units.minutes, minutes, language));
  }
  return parts.join(" ");
}

/** "Nov 1, 2026" in the language's date style; "—" for a missing or invalid date. */
export function formatDate(iso: string | null | undefined, language: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(latinLocale(language), { dateStyle: "medium" }).format(date);
}
