import { describe, it, expect } from 'vitest';
import { rawConfig, SECONDARY_COMPONENTS } from '@vie/shared/config';
import {
  TAB_PROP_SCHEMAS,
  SCHEMA_COMPONENT_NAMES,
} from '@/features/video-output/lib/tab-prop-schemas';

/** safeParse helper: returns success flag for a component's props schema. */
function parses(component: string, props: Record<string, unknown>): boolean {
  const entry = TAB_PROP_SCHEMAS[component];
  if (!entry) throw new Error(`No schema registered for component "${component}"`);
  return entry.schema.safeParse(props).success;
}

describe('tab-prop-schemas', () => {
  // ─── Parity: every backend-emittable component has a schema ───

  describe('schema ↔ domains.json parity', () => {
    const primary = rawConfig.components as string[];
    const secondary = SECONDARY_COMPONENTS as string[];

    it('should register a schema for every primary component', () => {
      const missing = primary.filter((name) => !SCHEMA_COMPONENT_NAMES.has(name));
      expect(missing).toEqual([]);
    });

    it('should register a schema for every secondary component', () => {
      const missing = secondary.filter((name) => !SCHEMA_COMPONENT_NAMES.has(name));
      expect(missing).toEqual([]);
    });

    it('should register the display_section fallback schema', () => {
      expect(SCHEMA_COMPONENT_NAMES.has('display_section')).toBe(true);
    });
  });

  // ─── Realistic assembler payloads (shapes mined from assembly/*.py) ───

  describe('accepts realistic assembler payloads', () => {
    it('spot_explorer — spots with day sections', () => {
      expect(
        parses('spot_explorer', {
          spots: [
            { name: 'Fushimi Inari', description: 'Thousand torii gates', emoji: '⛩️', cost: '¥0', rating: 4.8 },
            { name: 'Nishiki Market', description: '' , tips: 'Go early', timestamp: 322 },
          ],
          sections: [{ label: 'Day 1: Kyoto', spotIndices: [0, 1] }],
        }),
      ).toBe(true);
    });

    it('moment_track — points and spans', () => {
      expect(
        parses('moment_track', {
          items: [
            { label: 'Intro', time: '0:00', seconds: 0 },
            { label: 'Clutch play', time: '12:41', seconds: 761, endSeconds: 802, mood: 'hype', tags: ['highlight'] },
          ],
          filters: true,
        }),
      ).toBe(true);
    });

    it('comparison — rows + verdict from review extraction', () => {
      expect(
        parses('comparison', {
          pros: ['Battery life'],
          cons: ['Price'],
          comparisons: [
            { feature: 'Battery', thisProduct: '18h', competitor: '12h', competitorName: 'X200', winner: 'left' },
            { feature: 'Price', thisProduct: 999, competitor: 799, competitorName: 'X200' },
          ],
          leftLabel: 'M3 Air',
          rightLabel: 'X200',
          verdict: {
            badge: 'recommended',
            bottomLine: 'Best in class for travel.',
            bestFor: ['travelers'],
            notFor: ['gamers'],
            score: 8.5,
            maxScore: 10,
            subScores: [{ category: 'Battery', score: 9 }],
          },
        }),
      ).toBe(true);
    });

    it('checklist — labels with nullable notes (Python None → null)', () => {
      expect(
        parses('checklist', {
          items: [
            { label: '500 g pasta', note: null },
            { label: 'Olive oil', note: 'extra virgin', emoji: null },
          ],
          scalable: true,
          baseServings: 2,
        }),
      ).toBe(true);
    });

    it('step_player — steps with null timestamps', () => {
      expect(
        parses('step_player', {
          steps: [
            { number: 1, instruction: 'Boil water', timestamp: null, duration: null, tips: null },
            { number: 2, instruction: 'Add pasta', timestamp: 95, title: 'Cook', thumbnailUrl: 'https://x/f.jpg' },
          ],
        }),
      ).toBe(true);
    });

    it('flash_deck — cards with null emoji', () => {
      expect(
        parses('flash_deck', {
          cards: [{ front: 'Hooks', back: 'Functions that hook into React state', emoji: null }],
        }),
      ).toBe(true);
    });

    it('budget — total/currency/breakdown', () => {
      expect(
        parses('budget', {
          total: 3000,
          currency: 'USD',
          breakdown: [{ category: 'Lodging', amount: 1200, notes: 'hostels' }],
          savingTips: ['Book early'],
        }),
      ).toBe(true);
    });

    it('overview — nested data object', () => {
      expect(
        parses('overview', {
          data: {
            title: 'React Performance',
            emoji: '🧠',
            tldr: 'Profile first.',
            keyTakeaways: ['Measure before optimizing'],
            tips: ['Use the profiler'],
            duration: 2700,
            level: 'Advanced',
            stats: [{ label: 'Duration', value: '45 min', emoji: '⏱️' }],
          },
        }),
      ).toBe(true);
    });

    it('info_grid — key/value pairs with sections', () => {
      expect(
        parses('info_grid', {
          items: [
            { key: 'CPU', value: 'M2', evidence: 'benchmark segment' },
            { key: 'RAM', value: '16 GB' },
          ],
          sections: [{ label: 'Hardware', indices: [0, 1] }],
        }),
      ).toBe(true);
    });

    it('info_grid — accepts value-less headline items without remapping the tab', () => {
      // The assembler legitimately emits `""`, null, or no value at all for
      // key-only "headline chips"; one such row must not downgrade the whole
      // tab to display_section.
      expect(
        parses('info_grid', {
          items: [
            { key: 'Headline chip', value: '' },
            { key: 'Missing value' },
            { key: 'Null value', value: null },
            { key: 'Real row', value: 'substance' },
          ],
        }),
      ).toBe(true);
    });

    it('code_playground — snippets with null filename', () => {
      expect(
        parses('code_playground', {
          snippets: [
            { code: 'const [x] = useState(0)', language: 'tsx', explanation: 'State hook', filename: null, timestamp: 120 },
          ],
        }),
      ).toBe(true);
    });

    it('quiz_arena — standard and scenario questions', () => {
      expect(
        parses('quiz_arena', {
          questions: [
            { question: 'What is JSX?', options: ['Syntax', 'Library'], correctIndex: 0, explanation: 'A syntax extension' },
            { question: 'You see a re-render storm…', options: ['Memo it', 'Split state', 'Ignore'], correctIndex: 1, explanation: '', kind: 'scenario' },
          ],
        }),
      ).toBe(true);
    });

    it('packing_mission — items with weight/essential', () => {
      expect(
        parses('packing_mission', {
          items: [
            { item: 'Rain jacket', category: 'clothing', essential: true, weight: 0.4 },
            { item: 'Power bank' },
          ],
        }),
      ).toBe(true);
    });

    it('workout_room — normalized exercises + raw warmup passthrough', () => {
      expect(
        parses('workout_room', {
          exercises: [
            { name: 'Squat', emoji: '💪', formCues: ['Chest up'], modifications: ['Box squat'], sets: 3, reps: '8-10', rest: '90s' },
          ],
          warmup: [{ name: 'Jumping jacks', duration: '60s' }],
        }),
      ).toBe(true);
    });

    it('lyrics_karaoke — sections of timed lines', () => {
      expect(
        parses('lyrics_karaoke', {
          sections: [
            { name: 'Chorus', timestamp: 42, lines: [{ text: 'Hello from the other side', timestamp: 43 }] },
          ],
          artist: 'Adele',
        }),
      ).toBe(true);
    });

    it('video_filmstrip — frames', () => {
      expect(
        parses('video_filmstrip', {
          frames: [
            { thumbnailUrl: 'https://x/f0.jpg', timestamp: 10, caption: 'Arrival', sceneType: 'demo' },
            { thumbnailUrl: 'https://x/f1.jpg', timestamp: 95 },
          ],
        }),
      ).toBe(true);
    });

    it('claims_tracker — claims with status enum', () => {
      expect(
        parses('claims_tracker', {
          claims: [
            { claim: 'Inflation fell to 2.4%', source: 'Reporter', status: 'verified', sourceCitation: 'BLS' },
            { claim: 'Policy X caused it', source: 'Analyst', status: 'disputed', timestamp: 312 },
          ],
        }),
      ).toBe(true);
    });

    it('tier_list — items with optional suggested tier', () => {
      expect(
        parses('tier_list', {
          items: [
            { item: 'Ranged build', tier: 'S', reason: 'Safe damage' },
            { item: 'Tank build' },
          ],
        }),
      ).toBe(true);
    });

    it('formation_diagram — positions in 0-100 pitch space', () => {
      expect(
        parses('formation_diagram', {
          positions: [
            { player: 'Alisson', role: 'GK', x: 50, y: 5, number: 1 },
            { player: 'Van Dijk', x: 40, y: 25 },
            { player: 'Salah', x: 80, y: 80 },
          ],
          name: '4-3-3',
          team: 'Liverpool',
        }),
      ).toBe(true);
    });

    it('concept_canvas — typed and legacy-string connections', () => {
      expect(
        parses('concept_canvas', {
          concepts: [
            { name: 'Closure', emoji: '💡', definition: 'Function + captured scope', connections: [{ to: 'Scope', type: 'requires' }], group: 'Foundations' },
            { name: 'Scope', emoji: '💡', definition: 'Variable visibility', connections: ['Closure'], timestamp: 120 },
          ],
          groups: ['Foundations'],
        }),
      ).toBe(true);
    });

    it('connect_canvas — prompt/match pairs', () => {
      expect(
        parses('connect_canvas', {
          pairs: [
            { prompt: 'Closure', match: 'Scope' },
            { prompt: 'Promise', match: 'Async' },
          ],
        }),
      ).toBe(true);
    });

    it('stat_banner — equal-weight stats', () => {
      expect(
        parses('stat_banner', { stats: [{ label: 'Cores', value: '8', emoji: '⚙️' }] }),
      ).toBe(true);
    });

    it('tip_callout — text with style enum', () => {
      expect(parses('tip_callout', { text: 'Salt the water.', style: 'tip' })).toBe(true);
    });

    it('summary_header — one-line summary', () => {
      expect(parses('summary_header', { summary: 'A quick spec overview', title: 'In short' })).toBe(true);
    });

    it('diagram_card — nodes with index-addressed edges', () => {
      expect(
        parses('diagram_card', {
          nodes: [{ label: 'Client' }, { label: 'API', detail: 'Fastify gateway' }],
          edges: [{ source: 0, target: 1 }],
          caption: 'Request flow',
        }),
      ).toBe(true);
    });

    it('display_section — accepts arbitrary data', () => {
      expect(parses('display_section', { data: { anything: [1, 2, 3] } })).toBe(true);
    });

    it('frame_strip and quick_quiz — share primary contracts', () => {
      expect(parses('frame_strip', { frames: [{ thumbnailUrl: 'https://x/f.jpg', timestamp: 3 }] })).toBe(true);
      expect(parses('quick_quiz', { questions: [{ question: 'Q?', options: ['A', 'B'], correctIndex: 1 }] })).toBe(true);
    });
  });

  // ─── Malformed payload rejection ───

  describe('rejects malformed payloads', () => {
    it('should reject quiz question whose correctIndex is out of bounds', () => {
      expect(
        parses('quiz_arena', {
          questions: [{ question: 'Q?', options: ['A', 'B'], correctIndex: 2 }],
        }),
      ).toBe(false);
    });

    it('should reject a negative correctIndex', () => {
      expect(
        parses('quiz_arena', {
          questions: [{ question: 'Q?', options: ['A', 'B'], correctIndex: -1 }],
        }),
      ).toBe(false);
    });

    it('should accept quiz explanation and timestamp, including null from Python None', () => {
      expect(
        parses('quiz_arena', {
          questions: [
            { question: 'Q?', options: ['A', 'B'], correctIndex: 0, explanation: 'See (11:20).', timestamp: 680 },
            { question: 'R?', options: ['A', 'B'], correctIndex: 1, explanation: null, timestamp: null },
          ],
        }),
      ).toBe(true);
    });

    it('should reject a quiz explanation that is not a string', () => {
      expect(
        parses('quiz_arena', {
          questions: [{ question: 'Q?', options: ['A', 'B'], correctIndex: 0, explanation: ['not', 'text'] }],
        }),
      ).toBe(false);
    });

    it('should reject a quiz timestamp that is not a number', () => {
      expect(
        parses('quiz_arena', {
          questions: [{ question: 'Q?', options: ['A', 'B'], correctIndex: 0, timestamp: '11:20' }],
        }),
      ).toBe(false);
    });

    it('should reject quiz options with fewer than 2 entries', () => {
      expect(
        parses('quiz_arena', { questions: [{ question: 'Q?', options: ['A'], correctIndex: 0 }] }),
      ).toBe(false);
    });

    it('should reject diagram edges pointing past the node list', () => {
      expect(
        parses('diagram_card', {
          nodes: [{ label: 'A' }, { label: 'B' }],
          edges: [{ source: 0, target: 5 }],
        }),
      ).toBe(false);
    });

    it('should accept spot timestamp (seconds) and quote, including null from Python None', () => {
      expect(
        parses('spot_explorer', {
          spots: [
            { name: 'A', description: 'x', timestamp: 373, quote: 'Said verbatim.' },
            { name: 'B', description: 'y', timestamp: null, quote: null },
          ],
        }),
      ).toBe(true);
    });

    it('should reject a spot timestamp that is not a number', () => {
      expect(
        parses('spot_explorer', {
          spots: [{ name: 'A', description: 'x', timestamp: '6:13' }],
        }),
      ).toBe(false);
    });

    it('should reject a negative spot timestamp', () => {
      expect(
        parses('spot_explorer', {
          spots: [{ name: 'A', description: 'x', timestamp: -5 }],
        }),
      ).toBe(false);
    });

    it('should reject spot sections whose indices exceed the spot list', () => {
      expect(
        parses('spot_explorer', {
          spots: [{ name: 'A', description: 'x' }],
          sections: [{ label: 'Day 1', spotIndices: [0, 3] }],
        }),
      ).toBe(false);
    });

    it('should reject info_grid sections whose indices exceed the item list', () => {
      expect(
        parses('info_grid', {
          items: [{ key: 'k', value: 'v' }],
          sections: [{ label: 'S', indices: [4] }],
        }),
      ).toBe(false);
    });

    it('should reject formation positions outside the 0-100 pitch space', () => {
      expect(
        parses('formation_diagram', {
          positions: [
            { player: 'A', x: 120, y: 50 },
            { player: 'B', x: 10, y: 10 },
            { player: 'C', x: 20, y: 20 },
          ],
        }),
      ).toBe(false);
    });

    it('should reject a non-array items prop (string masquerading as list)', () => {
      expect(parses('checklist', { items: 'Pasta, Oil, Garlic' })).toBe(false);
    });

    it('should reject claim rows with an unknown status', () => {
      expect(
        parses('claims_tracker', {
          claims: [
            { claim: 'X', source: 'Y', status: 'confirmed' },
            { claim: 'Z', source: 'W', status: 'verified' },
          ],
        }),
      ).toBe(false);
    });

    it('should reject tier items with an out-of-enum tier', () => {
      expect(parses('tier_list', { items: [{ item: 'Build', tier: 'F' }] })).toBe(false);
    });

    it('should reject budget rows without a numeric amount', () => {
      expect(
        parses('budget', {
          total: 100,
          breakdown: [{ category: 'Food', amount: 'lots' }],
        }),
      ).toBe(false);
    });

    it('should reject moment items missing seconds', () => {
      expect(parses('moment_track', { items: [{ label: 'Intro', time: '0:00' }] })).toBe(false);
    });

    it('should reject comparison rows with a null side (0 stays valid)', () => {
      expect(
        parses('comparison', {
          comparisons: [{ feature: 'Price', thisProduct: null, competitor: '99' }],
        }),
      ).toBe(false);
      expect(
        parses('comparison', {
          comparisons: [{ feature: 'Price', thisProduct: 0, competitor: 99, competitorName: '' }],
        }),
      ).toBe(true);
    });
  });

  // ─── Unknown-key tolerance (assembler adds fields ahead of the frontend) ───

  describe('unknown-key passthrough', () => {
    it('should tolerate extra props keys and extra item fields', () => {
      expect(
        parses('checklist', {
          items: [{ label: 'Pasta', someFutureField: 42 }],
          futureTopLevel: { nested: true },
        }),
      ).toBe(true);
    });

    it('should tolerate frame-evidence fields on timestamped items', () => {
      expect(
        parses('step_player', {
          steps: [
            {
              number: 1,
              instruction: 'Cut the board',
              timestamp: 30,
              frameCaption: 'Saw on plank',
              frameEvidence: 'Shows the cut',
              frameOcr: '45°',
              frameSceneType: 'demo',
            },
          ],
        }),
      ).toBe(true);
    });
  });
});
