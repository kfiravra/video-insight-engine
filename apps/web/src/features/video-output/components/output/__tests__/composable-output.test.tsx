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
