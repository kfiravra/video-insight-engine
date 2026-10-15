/**
 * Onward links between assembled tabs (audit §2 #8). Pure helpers, kept out of
 * ComposableOutput.tsx so they stay unit-testable (component files export
 * only components — react-refresh rule).
 */
import type { TabEntry } from '@vie/types';
import { hasLeadingCount, inferItemCount, trueCountLabel } from '@/components/vie/cards/domain-meta';
import { stripLeadingEmoji } from '@/lib/string-utils';

export interface ResolvedCrossLink {
  targetTab: string;
  label: string;
  /** The target's goal — the reason to go there. */
  reason?: string;
}

/** The label a tab is shown under: emoji stripped, planner count replaced by
 *  the shipped count (R3). */
export function displayLabel(tab: TabEntry): string {
  return trueCountLabel(stripLeadingEmoji(tab.label), inferItemCount(tab));
}

function isContentTab(tab: TabEntry): boolean {
  return tab.component !== 'overview' && tab.id !== 'overview';
}

/** The candidate targets a link can reach: content tabs that exist, other
 *  than `self`, each once, in candidate order. The overview and ids no tab
 *  carries (a link from a stored row to a tab since dropped) go nowhere. */
function reachableTargets(tabs: TabEntry[], self: TabEntry, candidateIds: string[]): TabEntry[] {
  const seen = new Set<string>([self.id]);
  const targets: TabEntry[] = [];
  for (const id of candidateIds) {
    const target = tabs.find((t) => t.id === id);
    if (!target || seen.has(id) || !isContentTab(target)) continue;
    seen.add(id);
    targets.push(target);
  }
  return targets;
}

function toCrossLink(target: TabEntry): ResolvedCrossLink {
  return { targetTab: target.id, label: displayLabel(target), reason: target.goal };
}

/** The onward links of every tab. The assembler lets one tab claim each
 *  target and the hidden overview claims first, so content tabs ship almost
 *  none: each tab gets its explicit links plus the next tab in strip order,
 *  filtered to reachable content tabs (`reachableTargets`), labelled with the
 *  target's true-count label and its goal as the reason. A tab left with no
 *  link — e.g. a stored row whose only explicit link is the overview — wraps
 *  to the first content tab, so every content tab has a way onward. */
export function resolveCrossLinks(tabs: TabEntry[]): Map<string, ResolvedCrossLink[]> {
  const map = new Map<string, ResolvedCrossLink[]>();
  const firstContent = tabs.find(isContentTab);
  tabs.forEach((tab, i) => {
    const candidateIds = (tab.crossTabLinks ?? []).map((link) => link.targetTab);
    const next = tabs[i + 1];
    if (next) candidateIds.push(next.id);
    const targets = reachableTargets(tabs, tab, candidateIds);
    if (targets.length === 0 && firstContent && firstContent.id !== tab.id) targets.push(firstContent);
    map.set(tab.id, targets.map(toCrossLink));
  });
  return map;
}

export interface OverviewNavLink {
  targetTab: string;
  label: string;
  emoji?: string;
  count?: number;
}

/** Build the cross-tab nav grid shown inside the Overview tab. Sources sibling
 *  tabs from the assembled response, drops the overview itself, and annotates
 *  each entry with an emoji and the item count — unless the label already
 *  carries it ("24 Core Arguments"), so the count never prints twice. */
export function buildOverviewCrossTabLinks(
  allTabs: TabEntry[] | undefined,
  activeTabId: string,
): OverviewNavLink[] {
  if (!allTabs || allTabs.length === 0) return [];
  const siblings = allTabs.filter((t) => t.id !== activeTabId && t.component !== 'overview');
  return siblings.map((t) => {
    const label = displayLabel(t);
    return {
      targetTab: t.id,
      label,
      emoji: t.emoji || undefined,
      count: hasLeadingCount(label) ? undefined : inferItemCount(t),
    };
  });
}
