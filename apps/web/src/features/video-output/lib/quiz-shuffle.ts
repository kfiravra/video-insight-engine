/**
 * The order a quiz's options are shown in (output-quality G2-2, p3 review A1 / A4).
 *
 * Quiz v2 deals its keys on the server (`quiz_v2_select.shuffle_quiz`: in a quiz of k
 * questions no position keys more than ⌈k / 4⌉), so a quiz whose keys are already
 * spread renders in its stored order — a second shuffle here would undo that spread.
 * Any other quiz (a legacy or flag-off row: its keys cluster at option 0) is dealt
 * here the server's way: one deck of key positions per quiz, the wrong options
 * shuffled around each key. That deal is seeded by the quiz's shape — its question
 * count, each question's option count and timestamp — never by its text: translation
 * keeps the options' order and `correctIndex`, so the English and the source-language
 * docs show the same order, and an answer given before a language switch still points
 * at the same option after it.
 */

interface ShufflableQuestion {
  question: string;
  options: string[];
  correctIndex: number;
  timestamp?: number | null;
}

/** The four options quiz v2 writes: the positions a deal spreads keys over. */
const QUIZ_OPTIONS = 4;

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

/** Fisher–Yates on a copy. */
function shuffled<T>(items: T[], random: () => number): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function hasValidKey(question: ShufflableQuestion): boolean {
  const { options, correctIndex } = question;
  return options.length >= 2 && Number.isInteger(correctIndex) && correctIndex >= 0 && correctIndex < options.length;
}

/** True when no position keys more than ⌈k / 4⌉ of the k questions — the server's deal. */
function keysAreSpread(questions: ShufflableQuestion[]): boolean {
  if (questions.length < 2) return false;
  const counts = new Map<number, number>();
  for (const { correctIndex } of questions) counts.set(correctIndex, (counts.get(correctIndex) ?? 0) + 1);
  return Math.max(...counts.values()) <= Math.ceil(questions.length / QUIZ_OPTIONS);
}

/** The seed: the quiz's shape, which translation leaves alone (module comment). */
function shapeSeed(questions: ShufflableQuestion[]): number {
  return hashString(questions.map((q, i) => `${i}:${q.options.length}:${q.timestamp ?? ''}`).join('|'));
}

/** `count` key positions, each position dealt at most ⌈count / 4⌉ times. */
function keySlots(count: number, random: () => number): number[] {
  const positions = Array.from({ length: QUIZ_OPTIONS }, (_, i) => i);
  const deck = Array.from({ length: Math.ceil(count / QUIZ_OPTIONS) }, () => positions).flat();
  return shuffled(deck, random).slice(0, count);
}

/** The question with its keyed option at `slot` (modulo its option count) and the
 *  wrong ones shuffled around it; as is without a valid key. */
function placed<Q extends ShufflableQuestion>(question: Q, slot: number, random: () => number): Q {
  if (!hasValidKey(question)) return question;
  const { options, correctIndex } = question;
  const at = slot % options.length;
  const wrong = shuffled(
    options.filter((_, i) => i !== correctIndex),
    random,
  );
  return { ...question, options: [...wrong.slice(0, at), options[correctIndex], ...wrong.slice(at)], correctIndex: at };
}

/** The questions as the page shows them (module comment): a server-dealt quiz as
 *  stored, any other dealt evenly. The input is never mutated. */
export function dealQuizOptions<Q extends ShufflableQuestion>(questions: Q[]): Q[] {
  if (keysAreSpread(questions)) return questions;
  const random = mulberry32(shapeSeed(questions));
  const slots = keySlots(questions.length, random);
  return questions.map((question, i) => placed(question, slots[i], random));
}
