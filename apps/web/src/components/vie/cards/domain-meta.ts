/** Display metadata for content domains — identity-strip chip in VideoHero
 *  and the "Detected: …" moment in the streaming timeline — plus the tab-label
 *  helper the hero strip and the output share. Kept outside the component
 *  files so fast refresh keeps working (component files must only export
 *  components). */
import type { TabEntry } from '@vie/types';

export const DOMAIN_META: Record<string, { emoji: string; label: string }> = {
  learning: { emoji: '📚', label: 'Learning' },
  tech: { emoji: '💻', label: 'Tech' },
  food: { emoji: '🍳', label: 'Cooking' },
  travel: { emoji: '✈️', label: 'Travel' },
  fitness: { emoji: '💪', label: 'Fitness' },
  music: { emoji: '🎵', label: 'Music' },
  review: { emoji: '⭐', label: 'Review' },
  project: { emoji: '🛠️', label: 'Project' },
  language: { emoji: '🗣️', label: 'Language' },
  science: { emoji: '🔬', label: 'Science' },
  narrative: { emoji: '📖', label: 'Narrative' },
  podcast: { emoji: '🎙️', label: 'Podcast' },
  news: { emoji: '📰', label: 'News' },
  gaming: { emoji: '🎮', label: 'Gaming' },
  sport: { emoji: '⚽', label: 'Sport' },
};

/** A planner label's leading count: one to three digits followed by
 *  whitespace and a letter in any script ("9 Core Arguments", "12 רגעים").
 *  Hyphenated forms ("7-Minute Workout", "6-Step Factory") are names or
 *  durations and four-digit numbers ("2024 Review") are years — neither is a
 *  count, so neither matches. Shared with `stripCountPrefix` (the enter-mode
 *  context labels) so the app has exactly one notion of a count claim. */
export const LEADING_COUNT = /^\d{1,3}\s+(?=\p{L})/u;

/** Whether a label opens with a count claim (`LEADING_COUNT`). */
export function hasLeadingCount(label: string): boolean {
  return LEADING_COUNT.test(label);
}

/** Strip an assembler-produced count prefix ("8 Steps" → "Steps"). Returns
 *  the label unchanged when it carries no count, so callers can fall back to
 *  a default. */
export function stripCountPrefix(label: string): string {
  return label.replace(LEADING_COUNT, '');
}

/** Counts are true (R3, D7): the planner writes labels like "9 Core Arguments"
 *  before extraction, so the number is a guess. Replace it with the count the
 *  tab actually ships; when the count is unknown, drop the number rather than
 *  keep a lie. Labels without a leading count (including hyphenated names
 *  and years) are returned verbatim. */
export function trueCountLabel(label: string, count: number | undefined): string {
  if (!hasLeadingCount(label)) return label;
  const rest = stripCountPrefix(label);
  return count !== undefined ? `${count} ${rest}` : rest;
}

/** Component name → the props key holding its primary item list. The single
 *  source of truth for "how many things are inside this tab" — new components
 *  only need to register their count-prop here. */
const COUNT_PROP_BY_COMPONENT: Record<string, string> = {
  // Current (post video-to-action overhaul) component set. Retired keys
  // (quiz, timeline, clip_player, code_explorer, exercise_tracker, scenario,
  // gallery, lyrics_player) were dropped in interactive-overhaul-v2 1D.
  flash_deck: 'cards',
  step_player: 'steps',
  step_flow_canvas: 'steps',
  checklist: 'items',
  info_grid: 'items',
  spot_explorer: 'spots',
  moment_track: 'items',
  budget: 'breakdown',
  // Overhaul components
  code_playground: 'snippets',
  quiz_arena: 'questions',
  packing_mission: 'items',
  workout_room: 'exercises',
  video_filmstrip: 'frames',
  concept_canvas: 'concepts',
  connect_canvas: 'pairs',
  lyrics_karaoke: 'sections',
  claims_tracker: 'claims',
  tier_list: 'items',
  formation_diagram: 'positions',
};

/** Pull the most-meaningful item count out of an assembled tab's props.
 *  Returns undefined when the count is ambiguous — the nav card still renders
 *  without a number rather than guessing. Shared by the output (nav grid,
 *  cross-tab links) and the router, which stamps it on the hero strip's tab
 *  defs (`ShellTabDef.itemCount`). */
export function inferItemCount(tab: TabEntry): number | undefined {
  const props = tab.props ?? {};
  // Comparison (and its radar twin) has no single "items" list — it's pros +
  // cons + rows together.
  if (tab.component === 'comparison' || tab.component === 'comparison_radar') {
    const pros = Array.isArray(props.pros) ? props.pros.length : 0;
    const cons = Array.isArray(props.cons) ? props.cons.length : 0;
    const rows = Array.isArray(props.comparisons) ? props.comparisons.length : 0;
    const total = rows + pros + cons;
    return total > 0 ? total : undefined;
  }
  const key = COUNT_PROP_BY_COMPONENT[tab.component];
  if (!key) return undefined;
  const value = props[key];
  // moment_track historically also used `entries` — fall through when `items`
  // is missing so assembler outputs that used the older shape still show a count.
  if (!Array.isArray(value) && tab.component === 'moment_track') {
    const entries = props.entries;
    return Array.isArray(entries) ? entries.length : undefined;
  }
  return Array.isArray(value) ? value.length : undefined;
}
