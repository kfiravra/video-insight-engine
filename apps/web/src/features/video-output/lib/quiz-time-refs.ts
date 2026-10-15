/**
 * `(m:ss)` / `(h:mm:ss)` references the enrichment prompt embeds in quiz
 * explanation text — "…the Kodak Brownie (11:20)". QuizExplanation renders
 * each one as a seek chip (output-quality R12, 0a.7).
 */
// Minutes and seconds past the leading group are clock digits (00–59): a
// "(12:60)" is a ratio or a typo, never a seek target.
const TIME_REF = /\((\d{1,3}):([0-5]\d)(?::([0-5]\d))?\)/g;

export type ExplanationSegment = { kind: 'text'; text: string } | { kind: 'seek'; seconds: number };

/** Split explanation text into plain runs and seekable time references. */
export function splitTimeRefs(text: string): ExplanationSegment[] {
  const segments: ExplanationSegment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(TIME_REF)) {
    const [raw, a, b, c] = match;
    const seconds = c == null ? Number(a) * 60 + Number(b) : Number(a) * 3600 + Number(b) * 60 + Number(c);
    const start = match.index ?? 0;
    if (start > cursor) segments.push({ kind: 'text', text: text.slice(cursor, start) });
    segments.push({ kind: 'seek', seconds });
    cursor = start + raw.length;
  }
  if (cursor < text.length) segments.push({ kind: 'text', text: text.slice(cursor) });
  return segments;
}
