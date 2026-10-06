// A number, an optional range ("1-1.5", "8 to 10"), then a unit word. The negative lookahead keeps
// words that only start like a unit ("2 slices", "3 medium") from reading as one.
const DURATION_PATTERN =
  /(\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(\d+(?:\.\d+)?))?\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])/i;

/**
 * Seconds for a recipe/project/travel step's `duration`, or 0 when there is none.
 *
 * The extraction schema asks for a unit ("10 min"), but the model sometimes
 * returns a bare number. Steps are timed in minutes everywhere in the prompts
 * ("simmer 8 to 10 minutes" arrives as `10`), so a bare number is read as
 * minutes rather than seconds. A range keeps its upper bound for the same
 * reason ("1-1.5 hours" is 90 minutes).
 */
export function parseStepDurationSeconds(duration?: string | number | null): number {
  if (duration == null) return 0;
  if (typeof duration === 'number') return duration > 0 ? Math.round(duration * 60) : 0;
  const trimmed = duration.trim();
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Math.round(parseFloat(trimmed) * 60);
  const match = trimmed.match(DURATION_PATTERN);
  if (!match) return 0;
  const value = parseFloat(match[2] ?? match[1]);
  const unit = match[3].toLowerCase();
  if (unit.startsWith('h')) return Math.round(value * 3600);
  if (unit.startsWith('m')) return Math.round(value * 60);
  return Math.round(value);
}
