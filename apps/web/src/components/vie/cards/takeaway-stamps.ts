/**
 * Hero takeaways close with where they are said — "claim [20:45]", or
 * "claim [1:00:25]" past the hour (output-quality 2.2). HeroTakeaways renders
 * each marker as a seek chip and never as text; a takeaway without one stays
 * plain text.
 */
// Minutes and seconds past the leading group are clock digits (00–59): a
// "[12:60]" is not a moment in the video and stays in the text.
const STAMP = /\s*\[(\d{1,3}):([0-5]\d)(?::([0-5]\d))?\]/g;

export interface StampedTakeaway {
  /** The claim with every marker removed. */
  text: string;
  /** Each marker's position in seconds, in reading order. */
  stamps: number[];
}

/** Split a takeaway into its claim and the moments it cites. */
export function splitTakeawayStamps(takeaway: string): StampedTakeaway {
  const stamps: number[] = [];
  const text = takeaway.replace(STAMP, (_raw: string, a: string, b: string, c: string | undefined) => {
    stamps.push(c == null ? Number(a) * 60 + Number(b) : Number(a) * 3600 + Number(b) * 60 + Number(c));
    return '';
  });
  return { text: text.trim(), stamps };
}
