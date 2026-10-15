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

/** The onward links of every tab. The assembler lets one tab claim each
 *  target and the hidden overview claims first, so content tabs ship almost
 *  none: each tab gets its explicit links (overview dropped) plus the next tab
 *  in strip order, deduped by target, labelled with the target's true-count
 *  label and its goal as the reason. A content tab with no explicit link and
 *  no next tab wraps to the first content tab, so every content tab has at
 *  least one way onward. */
export function resolveCrossLinks(tabs: TabEntry[]): Map<string, ResolvedCrossLink[]> {
  const map = new Map<string, ResolvedCrossLink[]>();
  const firstContent = tabs.find(isContentTab);
  tabs.forEach((tab, i) => {
    const explicit = tab.crossTabLinks ?? [];
    const candidates = [...explicit];
    const next = tabs[i + 1];
    if (next) candidates.push({ targetTab: next.id, label: next.label });
    else if (explicit.length === 0 && firstContent && firstContent.id !== tab.id) {
      candidates.push({ targetTab: firstContent.id, label: firstContent.label });
    }
    const seen = new Set<string>([tab.id, 'overview']);
    const links: ResolvedCrossLink[] = [];
    for (const candidate of candidates) {
      if (seen.has(candidate.targetTab)) continue;
      seen.add(candidate.targetTab);
      const target = tabs.find((t) => t.id === candidate.targetTab);
      if (target?.component === 'overview') continue;
      links.push({
        targetTab: candidate.targetTab,
        label: target ? displayLabel(target) : candidate.label,
        reason: target?.goal,
      });
    }
    map.set(tab.id, links);
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
