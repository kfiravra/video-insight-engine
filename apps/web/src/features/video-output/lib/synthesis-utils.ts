import type { SynthesisResult, VIEResponseMeta } from "@vie/types";

/**
 * Reconstruct a SynthesisResult from a meta record (API response or streaming meta).
 * Returns null if meta is null or contains no synthesis fields.
 */
export function buildSynthesisFromMeta(
  meta: VIEResponseMeta | null,
): SynthesisResult | null {
  if (!meta) return null;
  const hasTldr = typeof meta.tldr === 'string' && meta.tldr.length > 0;
  const hasSummary = typeof meta.masterSummary === 'string' && meta.masterSummary.length > 0;
  if (!hasTldr && !hasSummary) return null;
  return {
    tldr: typeof meta.tldr === 'string' ? meta.tldr : '',
    keyTakeaways: Array.isArray(meta.keyTakeaways) ? meta.keyTakeaways as string[] : [],
    masterSummary: typeof meta.masterSummary === 'string' ? meta.masterSummary : '',
    seoDescription: typeof meta.seoDescription === 'string' ? meta.seoDescription : '',
    ...heroRewriteFields(meta),
  };
}

/**
 * The hero rewrite's own fields (output-quality 2.2), copied only when set —
 * stored-doc pages (the detail page, SharePage) render the hero from meta, and
 * older rows carry none of them.
 */
function heroRewriteFields(meta: VIEResponseMeta): Partial<SynthesisResult> {
  const fields: Partial<SynthesisResult> = {};
  if (typeof meta.brief === 'string' && meta.brief.length > 0) fields.brief = meta.brief;
  if (typeof meta.worthItFor === 'string' && meta.worthItFor.length > 0) fields.worthItFor = meta.worthItFor;
  if (typeof meta.skipIf === 'string' && meta.skipIf.length > 0) fields.skipIf = meta.skipIf;
  if (typeof meta.readingMinutes === 'number' && Number.isFinite(meta.readingMinutes) && meta.readingMinutes > 0) {
    fields.readingMinutes = meta.readingMinutes;
  }
  return fields;
}
