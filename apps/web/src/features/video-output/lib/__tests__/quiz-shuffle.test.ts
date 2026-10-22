import { describe, it, expect } from 'vitest';

import { shuffleQuizQuestion, shuffleQuizQuestions } from '../quiz-shuffle';

const OPTION_COUNT = 4;

function keyedZero(n: number) {
  return {
    question: `What does the speaker say about topic number ${n}?`,
    options: ['right', 'wrong a', 'wrong b', 'wrong c'].map((o) => `${o} ${n}`),
    correctIndex: 0,
  };
}

describe('shuffleQuizQuestion', () => {
  it('should return the same order when called twice for the same question', () => {
    const q = keyedZero(1);
    expect(shuffleQuizQuestion(q).options).toEqual(shuffleQuizQuestion({ ...q }).options);
  });

  it('should keep the correct option text under the new index', () => {
    const q = keyedZero(2);
    const out = shuffleQuizQuestion(q);
    expect(out.options[out.correctIndex]).toBe('right 2');
  });

  it('should keep every option exactly once', () => {
    const out = shuffleQuizQuestion(keyedZero(3));
    expect([...out.options].sort()).toEqual([...keyedZero(3).options].sort());
  });

  it('should not hold any answer position above half of 25 questions keyed at 0', () => {
    const positions = Array.from({ length: 25 }, (_, n) => shuffleQuizQuestion(keyedZero(n)).correctIndex);
    const counts = Array.from({ length: OPTION_COUNT }, (_, p) => positions.filter((x) => x === p).length);
    expect(Math.max(...counts) / 25).toBeLessThanOrEqual(0.5);
  });

  it('should not mutate the input question', () => {
    const q = keyedZero(4);
    const copy = JSON.parse(JSON.stringify(q));
    shuffleQuizQuestion(q);
    expect(q).toEqual(copy);
  });

  it('should pass a question with fewer than two options through unchanged', () => {
    const q = { question: 'q', options: ['only'], correctIndex: 0 };
    expect(shuffleQuizQuestion(q)).toBe(q);
  });

  it('should pass a question with an out-of-range correctIndex through unchanged', () => {
    const q = { question: 'q', options: ['a', 'b', 'c'], correctIndex: 5 };
    expect(shuffleQuizQuestion(q)).toBe(q);
  });

  it('should keep extra fields on the question', () => {
    const q = { ...keyedZero(5), explanation: 'why', timestamp: 12 };
    expect(shuffleQuizQuestion(q)).toMatchObject({ explanation: 'why', timestamp: 12 });
  });
});

describe('shuffleQuizQuestions', () => {
  it('should shuffle every question in the list', () => {
    const out = shuffleQuizQuestions([keyedZero(6), keyedZero(7)]);
    expect(out.map((q) => q.options[q.correctIndex])).toEqual(['right 6', 'right 7']);
  });
});
