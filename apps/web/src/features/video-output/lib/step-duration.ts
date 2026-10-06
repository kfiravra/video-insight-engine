/**
 * Seconds for a recipe/project step's `duration`, or 0 when there is none.
 *
 * The extraction schema asks for a unit ("10 min"), but the model sometimes
 * returns a bare number. Steps are timed in minutes everywhere in the prompts
 * ("simmer 8 to 10 minutes" arrives as `10`), so a bare number is read as
 * minutes rather than seconds.
 */
export function parseStepDurationSeconds(duration?: string | number | null): number {
  if (duration == null) return 0;
  if (typeof duration === 'number') return duration > 0 ? Math.round(duration * 60) : 0;
  const trimmed = duration.trim();
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Math.round(parseFloat(trimmed) * 60);
  const match = trimmed.match(/(\d+)\s*(min|minute|m|sec|second|s|hr|hour|h)/i);
  if (!match) return 0;
  const val = parseInt(match[1], 10);
  const unit = match[2].toLowerCase();
  if (unit.startsWith('h')) return val * 3600;
  if (unit.startsWith('m')) return val * 60;
  return val;
}
