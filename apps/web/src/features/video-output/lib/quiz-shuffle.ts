/**
 * Stable per-question option order (output-quality G2-2). Stored quizzes keep
 * the answer key at option 0, so every view permutes the options with a PRNG
 * seeded from the question text: the same question reads the same on every
 * render, reload and device, and the key stops sitting in one place.
 */

interface ShufflableQuestion {
  question: string;
  options: string[];
  correctIndex: number;
}

/** FNV-1a, 32-bit. */
function hashString(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/** mulberry32: a small seeded PRNG returning floats in [0, 1). */
function mulberry32(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Fisher–Yates over the indices 0..length-1. */
function permutation(length: number, random: () => number): number[] {
  const order = Array.from({ length }, (_, i) => i);
  for (let i = length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

/** The question with its options permuted and `correctIndex` following the
 *  correct option. Input is never mutated; a question with fewer than two
 *  options or an out-of-range key is returned as is. */
export function shuffleQuizQuestion<Q extends ShufflableQuestion>(question: Q): Q {
  const { options, correctIndex } = question;
  const valid = Number.isInteger(correctIndex) && correctIndex >= 0 && correctIndex < options.length;
  if (options.length < 2 || !valid) return question;
  const order = permutation(options.length, mulberry32(hashString(question.question)));
  return {
    ...question,
    options: order.map((from) => options[from]),
    correctIndex: order.indexOf(correctIndex),
  };
}

export function shuffleQuizQuestions<Q extends ShufflableQuestion>(questions: Q[]): Q[] {
  return questions.map(shuffleQuizQuestion);
}
