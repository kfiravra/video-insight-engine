import { describe, it, expect } from 'vitest';
import type { TabEntry } from '@vie/types';

import { buildOverviewCrossTabLinks, resolveCrossLinks } from '../cross-links';

function tabs(): TabEntry[] {
  return [
    { id: 'overview', label: 'Overview', emoji: '📚', component: 'overview', props: {} },
    {
      id: 'arguments',
      label: '9 Core Arguments',
      emoji: '🧠',
      component: 'spot_explorer',
      goal: 'Explore how the arguments connect',
      props: { spots: Array.from({ length: 24 }, (_, i) => ({ name: `Argument ${i + 1}` })) },
    },
    {
      id: 'numbers',
      label: 'Numbers That Matter',
      emoji: '📊',
      component: 'spot_explorer',
      goal: 'Check the numbers',
      props: { spots: [{ name: '150,000 lines' }] },
    },
    { id: 'quiz', label: 'Test Yourself', emoji: '✅', component: 'info_grid', props: { items: [{ key: 'Q1', value: 'A' }] } },
  ];
}

describe('resolveCrossLinks', () => {
  it('should link a tab without explicit links to the next tab in strip order', () => {
    const links = resolveCrossLinks(tabs()).get('arguments') ?? [];
    expect(links.map((l) => l.targetTab)).toEqual(['numbers']);
  });

  it('should wrap the last content tab to the first content tab when it has no explicit link', () => {
    const links = resolveCrossLinks(tabs()).get('quiz') ?? [];
    expect(links).toEqual([
      { targetTab: 'arguments', label: '24 Core Arguments', reason: 'Explore how the arguments connect' },
    ]);
  });

  it('should not wrap when the last tab carries an explicit link', () => {
    const all = tabs();
    all[3].crossTabLinks = [{ targetTab: 'numbers', label: 'Numbers That Matter' }];
    const links = resolveCrossLinks(all).get('quiz') ?? [];
    expect(links.map((l) => l.targetTab)).toEqual(['numbers']);
  });

  it('should skip a self link and the overview', () => {
    const all = tabs();
    all[1].crossTabLinks = [
      { targetTab: 'arguments', label: 'Me' },
      { targetTab: 'overview', label: 'Overview' },
    ];
    const links = resolveCrossLinks(all).get('arguments') ?? [];
    expect(links.map((l) => l.targetTab)).toEqual(['numbers']);
  });

  it('should drop a link to a tab that does not exist', () => {
    const all = tabs();
    all[1].crossTabLinks = [{ targetTab: 'ghost', label: 'Ghost Tab' }];
    const links = resolveCrossLinks(all).get('arguments') ?? [];
    expect(links.map((l) => l.targetTab)).toEqual(['numbers']);
  });

  it('should wrap the last tab when its only explicit link is the overview (stored rows)', () => {
    const all = tabs();
    all[3].crossTabLinks = [{ targetTab: 'overview', label: 'Overview' }];
    const links = resolveCrossLinks(all).get('quiz') ?? [];
    expect(links.map((l) => l.targetTab)).toEqual(['arguments']);
  });

  it('should wrap the last tab when its only explicit link targets an unknown tab', () => {
    const all = tabs();
    all[3].crossTabLinks = [{ targetTab: 'ghost', label: 'Ghost Tab' }];
    const links = resolveCrossLinks(all).get('quiz') ?? [];
    expect(links.map((l) => l.targetTab)).toEqual(['arguments']);
  });

  it('should leave a lone content tab without a link rather than linking it to itself', () => {
    const all = tabs().slice(0, 2);
    expect(resolveCrossLinks(all).get('arguments')).toEqual([]);
  });
});

describe('buildOverviewCrossTabLinks', () => {
  it('should drop the badge count when the label already carries the shipped count', () => {
    const links = buildOverviewCrossTabLinks(tabs(), 'overview');
    const args = links.find((l) => l.targetTab === 'arguments');
    expect(args).toEqual({ targetTab: 'arguments', label: '24 Core Arguments', emoji: '🧠', count: undefined });
  });

  it('should keep the badge count when the label has no count of its own', () => {
    const links = buildOverviewCrossTabLinks(tabs(), 'overview');
    const quiz = links.find((l) => l.targetTab === 'quiz');
    expect(quiz?.count).toBe(1);
  });
});
