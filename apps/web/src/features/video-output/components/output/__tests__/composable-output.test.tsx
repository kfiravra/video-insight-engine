import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { VIEResponse, TabEntry } from '@vie/types';

// Mock cross-tab link dependencies
vi.mock('../CrossTabLink', () => ({
  CrossTabLink: ({ label }: { label: string }) => <button>{label}</button>,
}));
vi.mock('../link-rules', () => ({
  resolveCrossTabLinks: () => ({}),
}));

import { ComposableOutput } from '../ComposableOutput';
import {
  getTelemetryCounter,
  resetTelemetryCounters,
} from '@/features/video-output/lib/telemetry';

const baseMeta = {
  videoId: 'v1',
  videoTitle: 'Test Video',
  creator: 'Tester',
  contentTags: ['travel' as const],
  modifiers: [],
  primaryTag: 'travel' as const,
  userGoal: 'Plan a trip',
};

function buildResponse(overrides: Partial<VIEResponse> = {}): VIEResponse {
  return {
    meta: baseMeta,
    tabs: [
      { id: 'itinerary', label: 'Itinerary', emoji: '\u{1F5FA}', dataSource: 'travel' },
      { id: 'budget', label: 'Budget', emoji: '\u{1F4B0}', dataSource: 'travel' },
    ],
    travel: {
      bestSeason: 'Spring',
      accommodationTips: [],
      transportationTips: [],
      itinerary: [],
      budget: { total: 3000, currency: 'USD', breakdown: [] },
      packingList: [],
    },
    ...overrides,
  };
}

describe('ComposableOutput', () => {
  it('should render content for the active tab', () => {
    const response = buildResponse();
    const { container } = render(
      <ComposableOutput
        response={response}
        activeTab="itinerary"
        onNavigateTab={vi.fn()}
      />,
    );

    // ComposableOutput renders either an interactive component or DisplaySection
    // The container should not be empty when a valid tab is active
    expect(container.innerHTML).not.toBe('');
  });

  it('should return null when active tab is not found', () => {
    const response = buildResponse();
    const { container } = render(
      <ComposableOutput
        response={response}
        activeTab="nonexistent"
        onNavigateTab={vi.fn()}
      />,
    );

    expect(container.innerHTML).toBe('');
  });

  it('should show fallback when domain data is missing', () => {
    const response = buildResponse({
      tabs: [{ id: 'analysis', label: 'Music Analysis', emoji: '\u{1F3B5}', dataSource: 'music.analysis' }],
      // no music data provided
    });

    render(
      <ComposableOutput
        response={response}
        activeTab="analysis"
        onNavigateTab={vi.fn()}
      />,
    );

    // DisplaySection renders "No data available for this section." when data is null
    expect(screen.getByText('No data available for this section.')).toBeInTheDocument();
  });

  it('should render DisplaySection for tabs without interactive mapping', () => {
    const response = buildResponse({
      tabs: [{ id: 'overview', label: 'Overview', emoji: '\u{1F4D6}', dataSource: 'travel' }],
    });

    const { container } = render(
      <ComposableOutput
        response={response}
        activeTab="overview"
        onNavigateTab={vi.fn()}
      />,
    );

    // "overview" is not in TAB_INTERACTIVE_MAP, so DisplaySection is used
    expect(container.innerHTML).not.toBe('');
  });

  it('should render content for interactive tab types', () => {
    const response = buildResponse({
      tabs: [{ id: 'ingredients', label: 'Ingredients', emoji: '\u{1F952}', dataSource: 'food' }],
      food: {
        meta: { servings: 4 },
        ingredients: [{ name: 'Pasta', amount: '500g' }],
        steps: [],
        tips: [],
        substitutions: [],
        nutrition: [],
        equipment: [],
      },
    });

    const { container } = render(
      <ComposableOutput
        response={response}
        activeTab="ingredients"
        onNavigateTab={vi.fn()}
      />,
    );

    // "ingredients" is in TAB_INTERACTIVE_MAP (ChecklistInteractive)
    expect(container.innerHTML).not.toBe('');
  });

  describe('v2 overview rendering', () => {
    function buildAssembledTabs(): TabEntry[] {
      return [
        {
          id: 'overview',
          label: 'Overview',
          emoji: '📋',
          component: 'overview',
          props: {
            data: {
              keyTakeaways: ['First insight', 'Second insight', 'Third insight'],
              level: 'Advanced',
              duration: 2700, // seconds — renderer converts to "45 min"
            },
          },
        },
        {
          id: 'quizzes',
          label: '🧪 Quizzes',
          emoji: '🧪',
          component: 'quiz_arena',
          // Schema-valid questions — tabs pass the 4.4 validation boundary
          // before count inference, so shorthand fixtures would remap to
          // display_section and lose their count.
          props: {
            questions: [
              { question: 'Q1?', options: ['A', 'B'], correctIndex: 0 },
              { question: 'Q2?', options: ['A', 'B'], correctIndex: 1 },
              { question: 'Q3?', options: ['A', 'B'], correctIndex: 0 },
            ],
          },
        },
        {
          id: 'concepts',
          label: 'Concepts',
          emoji: '🧠',
          component: 'info_grid',
          props: { items: [{ key: 'k1', value: 'v1' }, { key: 'k2', value: 'v2' }] },
        },
      ];
    }

    it('should derive crossTabLinks from sibling tabs and render the nav grid', () => {
      render(
        <ComposableOutput
          response={null}
          tabs={buildAssembledTabs()}
          activeTab="overview"
          onNavigateTab={vi.fn()}
          videoSummaryId="vsum-1"
        />,
      );
      expect(screen.getByText('Continue exploring')).toBeInTheDocument();
      // Sibling tab buttons appear with their stripped labels.
      expect(screen.getByRole('button', { name: 'Open Quizzes' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Open Concepts' })).toBeInTheDocument();
      // The active overview tab itself is excluded from the grid.
      expect(screen.queryByRole('button', { name: 'Open Overview' })).not.toBeInTheDocument();
    });

    it('should infer item counts for known sibling components', () => {
      render(
        <ComposableOutput
          response={null}
          tabs={buildAssembledTabs()}
          activeTab="overview"
          onNavigateTab={vi.fn()}
          videoSummaryId="vsum-1"
        />,
      );
      // Scope the count assertion inside each nav button so we don't collide
      // with the takeaway list's own "1/2/3" numbering elsewhere on the page.
      expect(
        within(screen.getByRole('button', { name: 'Open Quizzes' })).getByText('3'),
      ).toBeInTheDocument();
      expect(
        within(screen.getByRole('button', { name: 'Open Concepts' })).getByText('2'),
      ).toBeInTheDocument();
    });

    it('should print the shipped count in place of the planner count in nav-grid labels (R3)', () => {
      // "9 Core Arguments" over 24 spots — the planner guessed, the props are the truth.
      const tabs = buildAssembledTabs();
      tabs.push({
        id: 'arguments',
        label: '9 Core Arguments',
        emoji: '🧠',
        component: 'spot_explorer',
        props: { spots: Array.from({ length: 24 }, (_, i) => ({ name: `Argument ${i + 1}` })) },
      });
      render(
        <ComposableOutput
          response={null}
          tabs={tabs}
          activeTab="overview"
          onNavigateTab={vi.fn()}
          videoSummaryId="vsum-1"
        />,
      );
      expect(screen.getByRole('button', { name: 'Open 24 Core Arguments' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Open 9 Core Arguments' })).not.toBeInTheDocument();
    });

    it('should NOT render a duplicate hero card with title/subtitle/masterSummary or top takeaways', () => {
      const tabs = buildAssembledTabs();
      tabs[0].props = {
        data: {
          title: 'React Performance Masterclass',
          subtitle: 'A comprehensive guide…',
          masterSummary: 'Long-form duplicate of the page hero.',
          keyTakeaways: ['Profile before optimizing'],
        },
      };
      render(
        <ComposableOutput
          response={null}
          tabs={tabs}
          activeTab="overview"
          onNavigateTab={vi.fn()}
          videoSummaryId="vsum-1"
        />,
      );
      // The page hero (VideoHero) owns title/subtitle/masterSummary AND the
      // first six takeaways. Rendering any of them again here produced a
      // duplicate stack under the hero — see the brand-rule compliance pass.
      expect(screen.queryByText('React Performance Masterclass')).not.toBeInTheDocument();
      expect(screen.queryByText('A comprehensive guide…')).not.toBeInTheDocument();
      expect(screen.queryByText(/Long-form duplicate/)).not.toBeInTheDocument();
      expect(screen.queryByText('Profile before optimizing')).not.toBeInTheDocument();
    });
  });

  describe('cross-tab links (audit §2 #8)', () => {
    function linkedTabs(): TabEntry[] {
      return [
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
          goal: 'Check the numbers behind each argument',
          props: { spots: [{ name: '150,000 lines' }] },
        },
        {
          id: 'quiz',
          label: 'Test Yourself',
          emoji: '✅',
          component: 'info_grid',
          goal: 'See what stuck',
          props: { items: [{ key: 'Q1', value: 'A' }] },
        },
      ];
    }

    it('should link a tab without explicit links to the next tab, labelled with the target goal', () => {
      render(
        <ComposableOutput response={null} tabs={linkedTabs()} activeTab="arguments" onNavigateTab={vi.fn()} />,
      );
      const link = screen.getByRole('button', { name: /Check the numbers behind each argument/ });
      expect(link).toHaveTextContent('Next: Numbers That Matter');
    });

    it('should wrap the last tab to the first content tab so every tab has a way onward', () => {
      render(
        <ComposableOutput response={null} tabs={linkedTabs()} activeTab="quiz" onNavigateTab={vi.fn()} />,
      );
      expect(screen.getByRole('button', { name: /Explore how the arguments connect/ })).toHaveTextContent(
        'Next: 24 Core Arguments',
      );
    });

    it('should print the true count in an explicit assembler link label', () => {
      const tabs = linkedTabs();
      tabs[1].crossTabLinks = [{ targetTab: 'arguments', label: '9 Core Arguments' }];
      render(<ComposableOutput response={null} tabs={tabs} activeTab="numbers" onNavigateTab={vi.fn()} />);
      expect(screen.getByRole('button', { name: /Explore how the arguments connect/ })).toHaveTextContent(
        'Next: 24 Core Arguments',
      );
    });

    it('should render one link per target when an explicit link and the next tab coincide', () => {
      const tabs = linkedTabs();
      tabs[0].crossTabLinks = [{ targetTab: 'numbers', label: 'Numbers That Matter' }, { targetTab: 'overview', label: 'Overview' }];
      render(<ComposableOutput response={null} tabs={tabs} activeTab="arguments" onNavigateTab={vi.fn()} />);
      expect(screen.getAllByRole('button', { name: /Next:/ })).toHaveLength(1);
    });

    it('should navigate to the target when a link is clicked', async () => {
      const user = userEvent.setup();
      const onNavigateTab = vi.fn();
      render(
        <ComposableOutput response={null} tabs={linkedTabs()} activeTab="numbers" onNavigateTab={onNavigateTab} />,
      );
      await user.click(screen.getByRole('button', { name: /See what stuck/ }));
      expect(onNavigateTab).toHaveBeenCalledWith('quiz');
    });
  });

  describe('enter mode / FlowPlayer (interactive-overhaul-v2 P4)', () => {
    function foodTabs(): TabEntry[] {
      return [
        {
          id: 'ingredients',
          label: '3 Ingredients',
          emoji: '🛒',
          component: 'checklist',
          props: {
            items: [{ label: 'Pasta' }, { label: 'Olive oil' }, { label: 'Garlic' }],
            scalable: true,
            baseServings: 2,
          },
        },
        {
          id: 'steps',
          label: 'Steps',
          emoji: '👨‍🍳',
          component: 'step_player',
          props: {
            steps: [
              { number: 1, title: 'Boil Water', instruction: 'Bring water to a boil.' },
              { number: 2, title: 'Cook Pasta', instruction: 'Add pasta, cook al dente.' },
            ],
          },
        },
      ];
    }

    it('shows the "Enter Cooking Mode" button on the food path', () => {
      render(
        <ComposableOutput
          response={null}
          tabs={foodTabs()}
          activeTab="steps"
          onNavigateTab={vi.fn()}
          primaryTag="food"
        />,
      );
      expect(screen.getByRole('button', { name: /Enter Cooking Mode/i })).toBeInTheDocument();
    });

    it('launches the cooking flow with ingredients context + steps sequence', async () => {
      const user = userEvent.setup();
      render(
        <ComposableOutput
          response={null}
          tabs={foodTabs()}
          activeTab="steps"
          onNavigateTab={vi.fn()}
          primaryTag="food"
        />,
      );
      await user.click(screen.getByRole('button', { name: /Enter Cooking Mode/i }));

      // Cooking Mode header + step content (sequence) render.
      expect(screen.getByText('Cooking Mode')).toBeInTheDocument();
      expect(screen.getByText('Boil Water')).toBeInTheDocument();
      // Progress counter reflects the steps sequence (2 steps).
      expect(screen.getByText('0/2 steps')).toBeInTheDocument();
      // Ingredient context is present (Pasta is one of the checklist items).
      expect(screen.getAllByText('Pasta').length).toBeGreaterThan(0);
      // Exit affordance uses the cooking-mode aria-label (unchanged).
      expect(screen.getByRole('button', { name: /exit cooking mode/i })).toBeInTheDocument();
    });

    it('shows the "Enter Cooking Mode" button when the steps tab is a step_flow_canvas', () => {
      // Real recipes have 8+ steps, which assembly ships as step_flow_canvas —
      // the tabs also pass through the Zod boundary here, as cached outputs do.
      const [ingredients, steps] = foodTabs();
      const longSteps = Array.from({ length: 9 }, (_, i) => ({
        number: i + 1,
        title: `Step ${i + 1}`,
        instruction: `Do thing ${i + 1}`,
      }));
      render(
        <ComposableOutput
          response={null}
          tabs={[ingredients, { ...steps, component: 'step_flow_canvas', props: { steps: longSteps } }]}
          activeTab="ingredients"
          onNavigateTab={vi.fn()}
          primaryTag="food"
        />,
      );
      expect(screen.getByRole('button', { name: /Enter Cooking Mode/i })).toBeInTheDocument();
    });

    it('does NOT show an enter-mode button for a non-food domain lacking the required tabs', () => {
      render(
        <ComposableOutput
          response={null}
          tabs={[
            { id: 'analysis', label: 'Analysis', emoji: '🎵', component: 'info_grid', props: { items: [{ key: 'k', value: 'v' }] } },
          ]}
          activeTab="analysis"
          onNavigateTab={vi.fn()}
          primaryTag="music"
        />,
      );
      expect(screen.queryByRole('button', { name: /Enter .* Mode/i })).not.toBeInTheDocument();
    });
  });

  describe('cached-tabs validation boundary (4.4 follow-up)', () => {
    let warnSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      resetTelemetryCounters();
      warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    });

    afterEach(() => {
      warnSpy.mockRestore();
      resetTelemetryCounters();
    });

    // REST-fetched assembledTabs (cached videos) never pass through
    // handleTabReady — this proves the ComposableOutput boundary gates them.
    it('should render the display_section fallback and count drift for a malformed cached tab', () => {
      const tabs: TabEntry[] = [
        {
          id: 'ingredients',
          label: 'Ingredients',
          emoji: '🧅',
          component: 'checklist',
          // String masquerading as the items list — fails the checklist schema.
          props: { items: 'Pasta, Oil, Garlic' },
        },
      ];
      render(
        <ComposableOutput
          response={null}
          tabs={tabs}
          activeTab="ingredients"
          onNavigateTab={vi.fn()}
        />,
      );
      // Fallback path: DisplaySection prints the raw payload; the checklist
      // interactive (checkbox list) must NOT mount on malformed props.
      expect(screen.getByText('Pasta, Oil, Garlic')).toBeInTheDocument();
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
      expect(getTelemetryCounter('tab_props_invalid')).toBe(1);
      expect(getTelemetryCounter('tab_props_invalid.checklist')).toBe(1);
    });

    it('should render a valid cached tab through its component without counting', () => {
      const tabs: TabEntry[] = [
        {
          id: 'ingredients',
          label: 'Ingredients',
          emoji: '🧅',
          component: 'checklist',
          props: { items: [{ label: 'Pasta' }] },
        },
      ];
      render(
        <ComposableOutput
          response={null}
          tabs={tabs}
          activeTab="ingredients"
          onNavigateTab={vi.fn()}
        />,
      );
      expect(screen.getByText('Pasta')).toBeInTheDocument();
      expect(getTelemetryCounter('tab_props_invalid')).toBe(0);
      expect(getTelemetryCounter('tab_component_unknown')).toBe(0);
    });
  });

  describe('secondary attachments (interactive-overhaul-v2 P2)', () => {
    function tabWithAttachments(): TabEntry[] {
      return [
        {
          id: 'specs',
          label: 'Specs',
          emoji: '📊',
          component: 'info_grid',
          props: { items: [{ key: 'CPU', value: 'M2' }] },
          attachments: [
            {
              slot: 'top',
              component: 'summary_header',
              props: { summary: 'A quick spec overview', title: 'In short' },
            },
            {
              slot: 'bottom',
              component: 'stat_banner',
              props: { stats: [{ label: 'Cores', value: '8' }] },
            },
          ],
        },
      ];
    }

    it('renders top and bottom attachments around the primary', () => {
      render(
        <ComposableOutput
          response={null}
          tabs={tabWithAttachments()}
          activeTab="specs"
          onNavigateTab={vi.fn()}
        />,
      );
      // Primary
      expect(screen.getByText('CPU')).toBeInTheDocument();
      // Top attachment (summary_header)
      expect(screen.getByText('A quick spec overview')).toBeInTheDocument();
      // Bottom attachment (stat_banner)
      expect(screen.getByText('Cores')).toBeInTheDocument();
      expect(screen.getByText('8')).toBeInTheDocument();
    });

    it('should print the goal once when a summary_header attachment is present (R4)', () => {
      const [tab] = tabWithAttachments();
      const goal = 'A quick spec overview';
      // Stored v10 docs: the summary_header text IS the goal.
      render(
        <ComposableOutput
          response={null}
          tabs={[{ ...tab, goal }]}
          activeTab="specs"
          onNavigateTab={vi.fn()}
        />,
      );
      expect(screen.getAllByText(goal)).toHaveLength(1);
      expect(screen.getByRole('note')).toHaveTextContent(goal);
    });

    it('should keep the intro goal line when the summary_header carries a blank summary', () => {
      const [tab] = tabWithAttachments();
      const goal = 'Spec the machine at a glance';
      const blankHeader = { slot: 'top' as const, component: 'summary_header', props: { summary: '   ' } };
      render(
        <ComposableOutput
          response={null}
          tabs={[{ ...tab, goal, attachments: [blankHeader] }]}
          activeTab="specs"
          onNavigateTab={vi.fn()}
        />,
      );
      // The header prints nothing for a blank summary, so the intro carries the goal.
      expect(screen.getAllByText(goal)).toHaveLength(1);
      expect(screen.queryByRole('note')).not.toBeInTheDocument();
    });

    it('should keep the intro goal line when the summary_header sits in the bottom slot', () => {
      const [tab] = tabWithAttachments();
      const goal = 'Spec the machine at a glance';
      const bottomHeader = { slot: 'bottom' as const, component: 'summary_header', props: { summary: goal } };
      render(
        <ComposableOutput
          response={null}
          tabs={[{ ...tab, goal, attachments: [bottomHeader] }]}
          activeTab="specs"
          onNavigateTab={vi.fn()}
        />,
      );
      // Intro at the top AND the bottom header: the goal opens the tab either way.
      const goalNodes = screen.getAllByText(goal);
      expect(goalNodes).toHaveLength(2);
      expect(goalNodes[0].closest('[role="note"]')).toBeNull();
    });

    it('should keep the intro goal line on a tab without a summary_header', () => {
      const goal = 'Spec the machine at a glance';
      render(
        <ComposableOutput
          response={null}
          tabs={[{ id: 'specs', label: 'Specs', emoji: '📊', component: 'info_grid', goal, props: { items: [{ key: 'CPU', value: 'M2' }] } }]}
          activeTab="specs"
          onNavigateTab={vi.fn()}
        />,
      );
      expect(screen.getByText(goal)).toBeInTheDocument();
    });

    it('renders flat tabs (no attachments) exactly as before', () => {
      const tabs: TabEntry[] = [
        {
          id: 'specs',
          label: 'Specs',
          emoji: '📊',
          component: 'info_grid',
          props: { items: [{ key: 'CPU', value: 'M2' }] },
        },
      ];
      render(
        <ComposableOutput
          response={null}
          tabs={tabs}
          activeTab="specs"
          onNavigateTab={vi.fn()}
        />,
      );
      expect(screen.getByText('CPU')).toBeInTheDocument();
      // No attachment chrome leaks in.
      expect(screen.queryByRole('note')).not.toBeInTheDocument();
    });

    it('ignores attachments referencing an unknown component', () => {
      const tabs: TabEntry[] = [
        {
          id: 'specs',
          label: 'Specs',
          emoji: '📊',
          component: 'info_grid',
          props: { items: [{ key: 'CPU', value: 'M2' }] },
          attachments: [
            { slot: 'top', component: 'does_not_exist', props: {} },
          ],
        },
      ];
      const { container } = render(
        <ComposableOutput
          response={null}
          tabs={tabs}
          activeTab="specs"
          onNavigateTab={vi.fn()}
        />,
      );
      // Primary still renders; unknown attachment is skipped silently.
      expect(screen.getByText('CPU')).toBeInTheDocument();
      expect(container.innerHTML).not.toBe('');
    });
  });
});
