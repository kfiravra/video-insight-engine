import { describe, it, expect } from 'vitest';

import { dealQuizOptions } from '../quiz-shuffle';

const OPTION_COUNT = 4;

/** A legacy (flag-off / pre-3.0) question: the key stored at option 0. */
function keyedZero(n: number) {
  return {
    question: `What does the speaker say about topic number ${n}?`,
    options: ['right', 'wrong a', 'wrong b', 'wrong c'].map((o) => `${o} ${n}`),
    correctIndex: 0,
    timestamp: 60 * n,
  };
}

function legacyQuiz(count: number) {
  return Array.from({ length: count }, (_, n) => keyedZero(n));
}

/** The same quiz after translation: new texts, the same order, key and stamps. */
function translated<Q extends { question: string; options: string[] }>(questions: Q[]): Q[] {
  return questions.map((q) => ({ ...q, question: `[es] ${q.question}`, options: q.options.map((o) => `[es] ${o}`) }));
}

/** A quiz v2 row: the server dealt its keys to positions 2, 0, 1. */
const SERVER_DEALT = [
  { question: 'Why pay the card first?', options: ['Cards are forgiven', 'Funds never grow', 'Its rate beats the fund', 'The bank asks'], correctIndex: 2 },
  { question: 'What goes into the buffer?', options: ['One month of expenses', 'A year of rent', 'Every spare dollar', 'Nothing at all'], correctIndex: 0 },
  { question: 'When does the dollar go to the fund?', options: ['Before the card', 'After the card is paid', 'Every second month', 'Only in a bad year'], correctIndex: 1 },
];

function positionCounts(keys: number[]): number[] {
  return Array.from({ length: OPTION_COUNT }, (_, p) => keys.filter((k) => k === p).length);
}

describe('dealQuizOptions', () => {
  describe('a quiz the server dealt', () => {
    it('should return the stored order when no position keys more than a quarter of the questions', () => {
      expect(dealQuizOptions(SERVER_DEALT)).toEqual(SERVER_DEALT);
    });

    it('should return the stored order when an eight-question quiz keys each position twice', () => {
      const quiz = legacyQuiz(8).map((q, n) => ({ ...q, correctIndex: n % OPTION_COUNT }));
      expect(dealQuizOptions(quiz)).toEqual(quiz);
    });
  });

  describe('a legacy quiz (keys clustered)', () => {
    it('should key three different positions when three questions are all keyed at 0', () => {
      const keys = dealQuizOptions(legacyQuiz(3)).map((q) => q.correctIndex);
      expect(new Set(keys).size).toBe(3);
    });

    it('should key no position more than twice when eight questions are all keyed at 0', () => {
      const keys = dealQuizOptions(legacyQuiz(8)).map((q) => q.correctIndex);
      expect(Math.max(...positionCounts(keys))).toBe(2);
    });

    it('should deal a quiz whose keys mostly sit at one position', () => {
      const quiz = legacyQuiz(5).map((q, n) => ({ ...q, correctIndex: n === 4 ? 1 : 0 }));
      const keys = dealQuizOptions(quiz).map((q) => q.correctIndex);
      expect(Math.max(...positionCounts(keys))).toBeLessThanOrEqual(2);
    });

    it('should keep the correct option text under the new index', () => {
      const dealt = dealQuizOptions(legacyQuiz(5));
      expect(dealt.map((q) => q.options[q.correctIndex])).toEqual(['right 0', 'right 1', 'right 2', 'right 3', 'right 4']);
    });

    it('should keep every option exactly once', () => {
      const [first] = dealQuizOptions(legacyQuiz(3));
      expect([...first.options].sort()).toEqual([...keyedZero(0).options].sort());
    });

    it('should deal the same order when the question texts are translated', () => {
      const keys = (quiz: ReturnType<typeof legacyQuiz>) => dealQuizOptions(quiz).map((q) => q.correctIndex);
      expect(keys(translated(legacyQuiz(5)))).toEqual(keys(legacyQuiz(5)));
    });

    it('should deal the same order when called twice for the same quiz', () => {
      expect(dealQuizOptions(legacyQuiz(5))).toEqual(dealQuizOptions(legacyQuiz(5)));
    });

    it('should not mutate the input quiz', () => {
      const quiz = legacyQuiz(3);
      const copy = JSON.parse(JSON.stringify(quiz));
      dealQuizOptions(quiz);
      expect(quiz).toEqual(copy);
    });

    it('should pass a question with fewer than two options through unchanged', () => {
      const lone = { question: 'q', options: ['only'], correctIndex: 0 };
      expect(dealQuizOptions([lone, lone])[0]).toBe(lone);
    });

    it('should pass a question with an out-of-range correctIndex through unchanged', () => {
      const broken = { question: 'q', options: ['a', 'b', 'c'], correctIndex: 5 };
      expect(dealQuizOptions([broken, broken])[0]).toBe(broken);
    });

    it('should keep extra fields on the question', () => {
      const [first] = dealQuizOptions([{ ...keyedZero(0), explanation: 'why' }, keyedZero(1)]);
      expect(first).toMatchObject({ explanation: 'why', timestamp: 0 });
    });
  });
});
