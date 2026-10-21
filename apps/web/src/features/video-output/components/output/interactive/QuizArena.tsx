import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  GlassCard,
  FadeIn,
  OptionGrid,
  InlineScore,
  Shake,
  BackForward,
  Stepper,
  ScoreRing,
  Badge,
  EmojiMarker,
  VisualEvidence,
} from '@/components/vie';
import { HelpCircle } from 'lucide-react';

import { Celebration } from '../Celebration';
import { EmptyTabState } from './EmptyTabState';
import { QuizExplanation } from './QuizExplanation';
import { useLabels } from '@/lib/i18n';
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion';

export interface QuizArenaQuestion {
  question: string;
  options: string[];
  correctIndex: number;
  explanation?: string;
  context?: string;
  kind?: 'standard' | 'scenario';
  thumbnailUrl?: string;
  frameCaption?: string;
  /** Where the transcript gives the answer, in seconds (output-quality 1.13):
   *  the frame evidence's seek and, once answered, the explanation's chip. */
  timestamp?: number;
  /** The speaker's words that settle the answer (quiz v2, output-quality 2.1b):
   *  shown under the explanation once answered. */
  quote?: string | null;
  /** The quote is the English rendering of non-English speech (2.0 d, D12): prose. */
  quoteTranslated?: boolean;
}

interface QuizArenaProps {
  questions: QuizArenaQuestion[];
  tabId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
  videoId?: string;
  /** Per-question countdown. Off by default — a recall quiz is untimed
   *  (output-quality R12/D9); opt in for drills. */
  withTimer?: boolean;
  /** Seconds allotted per question when timer is on. */
  timerSeconds?: number;
  /** Seek callback for evidence-frame jump-to-moment, the explanation's
   *  `(m:ss)` chips and the question's answer timestamp. */
  onSeek?: (seconds: number) => void;
}

const DEFAULT_TIMER_SECONDS = 10;

interface CountdownRingProps {
  seconds: number;
  active: boolean;
  onExpire: () => void;
}

// Reset-per-question happens via `key={currentIndex}` at the call site: a new
// question remounts the ring, re-seeding `remaining` and the expired latch —
// no reset effect needed (react-hooks: avoid setState-in-effect resets).
const CountdownRing = memo(function CountdownRing({
  seconds,
  active,
  onExpire,
}: CountdownRingProps) {
  const reducedMotion = usePrefersReducedMotion();
  const [remaining, setRemaining] = useState(seconds);
  const expiredRef = useRef(false);

  useEffect(() => {
    if (!active) return;
    if (remaining <= 0) {
      if (!expiredRef.current) {
        expiredRef.current = true;
        onExpire();
      }
      return;
    }
    const id = setTimeout(() => setRemaining((r) => Math.max(0, r - 1)), 1000);
    return () => clearTimeout(id);
  }, [active, remaining, onExpire]);

  const fraction = seconds > 0 ? remaining / seconds : 0;
  const deg = Math.round(fraction * 360);
  const isUrgent = remaining <= 3;

  return (
    <div
      data-slot="countdown-ring"
      role="timer"
      aria-label={`${remaining} seconds remaining`}
      className={cn(
        'relative inline-flex h-9 w-9 items-center justify-center rounded-full',
        'transition-colors duration-200',
      )}
      style={{
        background: reducedMotion
          ? 'transparent'
          : `conic-gradient(${isUrgent ? 'var(--destructive)' : 'var(--vie-accent)'} ${deg}deg, oklch(from var(--muted) l c h / 0.4) 0deg)`,
      }}
    >
      <span
        className={cn(
          'flex h-7 w-7 items-center justify-center rounded-full bg-background text-[11px] font-semibold tabular-nums',
          isUrgent && 'text-destructive',
        )}
      >
        {remaining}
      </span>
    </div>
  );
});

/**
 * Unified quiz + scenario component. Single question per screen with opt-in
 * timer ring, streak counter, frame-evidence slot, and localStorage best
 * score persistence (only when `videoId` is provided).
 */
export const QuizArena = memo(function QuizArena({
  questions,
  nextTab,
  onNavigateTab,
  videoId,
  withTimer = false,
  timerSeconds = DEFAULT_TIMER_SECONDS,
  onSeek,
}: QuizArenaProps) {
  const t = useLabels();
  const reducedMotion = usePrefersReducedMotion();
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Map<number, number>>(new Map());
  const [shaking, setShaking] = useState(false);
  const [streak, setStreak] = useState(0);
  const [maxStreak, setMaxStreak] = useState(0);
  const [pulseStreak, setPulseStreak] = useState(false);
  const [bestScore, setBestScore] = useState<number | null>(null);
  // Screen-reader announcement for answer feedback — the visual feedback
  // (option colors, shake, streak pulse) is otherwise not perceivable
  // non-visually. Set only from answer events so revisiting an answered
  // question never re-announces.
  const [liveMessage, setLiveMessage] = useState('');
  const shakeTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pulseTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const storageKey = videoId ? `vie:quiz:${videoId}` : null;

  // Load best score on mount.
  useEffect(() => {
    if (!storageKey) return;
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw != null) {
        const n = parseInt(raw, 10);
        if (!Number.isNaN(n)) setBestScore(n);
      }
    } catch {
      // ignore
    }
  }, [storageKey]);

  useEffect(() => () => {
    if (shakeTimerRef.current) clearTimeout(shakeTimerRef.current);
    if (pulseTimerRef.current) clearTimeout(pulseTimerRef.current);
  }, []);

  const answeredSteps = useMemo(() => new Set(answers.keys()), [answers]);

  const recordAnswer = useCallback(
    (optionIndex: number, isCorrect: boolean) => {
      if (answers.has(currentIndex)) return;
      setAnswers((prev) => {
        const next = new Map(prev);
        next.set(currentIndex, optionIndex);
        return next;
      });
      const question = questions[currentIndex];
      const correctText = question?.options[question.correctIndex] ?? '';
      if (isCorrect) {
        setLiveMessage('Correct!');
        setStreak((prev) => {
          const nextStreak = prev + 1;
          setMaxStreak((m) => Math.max(m, nextStreak));
          return nextStreak;
        });
        setPulseStreak(true);
        if (pulseTimerRef.current) clearTimeout(pulseTimerRef.current);
        pulseTimerRef.current = setTimeout(() => setPulseStreak(false), 400);
      } else {
        setLiveMessage(
          optionIndex === -1
            ? `Time is up. The correct answer is ${correctText}.`
            : `Incorrect. The correct answer is ${correctText}.`,
        );
        setStreak(0);
        setShaking(true);
        if (shakeTimerRef.current) clearTimeout(shakeTimerRef.current);
        shakeTimerRef.current = setTimeout(() => setShaking(false), 400);
      }
    },
    [answers, currentIndex, questions],
  );

  const handleSelect = useCallback(
    (optionIndex: number) => {
      const q = questions[currentIndex];
      if (!q) return;
      const isCorrect = q.correctIndex === optionIndex;
      recordAnswer(optionIndex, isCorrect);
    },
    [currentIndex, questions, recordAnswer],
  );

  const handleTimerExpire = useCallback(() => {
    if (answers.has(currentIndex)) return;
    // Record -1 as a sentinel for "timed out" (won't match any correctIndex).
    recordAnswer(-1, false);
  }, [answers, currentIndex, recordAnswer]);

  const score = useMemo(() => {
    let correct = 0;
    answers.forEach((picked, qIdx) => {
      if (questions[qIdx]?.correctIndex === picked) correct++;
    });
    return correct;
  }, [answers, questions]);

  const allAnswered = questions.length > 0 && answers.size === questions.length;

  // Persist best score on completion.
  useEffect(() => {
    if (!allAnswered || !storageKey) return;
    if (bestScore == null || score > bestScore) {
      try {
        window.localStorage.setItem(storageKey, String(score));
        setBestScore(score);
      } catch {
        // ignore
      }
    }
  }, [allAnswered, score, bestScore, storageKey]);

  const wrongAnswers = useMemo(() => {
    if (!allAnswered) return [];
    const wrong: Array<{ index: number; question: string; picked: string; correct: string }> = [];
    answers.forEach((picked, qIdx) => {
      const q = questions[qIdx];
      if (!q || q.correctIndex === picked) return;
      wrong.push({
        index: qIdx,
        question: q.question,
        picked: picked >= 0 ? q.options[picked] ?? '?' : '(timed out)',
        correct: q.options[q.correctIndex] ?? '?',
      });
    });
    return wrong;
  }, [allAnswered, answers, questions]);

  const handleReset = useCallback(() => {
    setCurrentIndex(0);
    setAnswers(new Map());
    setStreak(0);
    setMaxStreak(0);
    setShaking(false);
    setPulseStreak(false);
    setLiveMessage('');
  }, []);

  if (questions.length === 0)
    return <EmptyTabState message="No quiz questions were generated for this video." icon={HelpCircle} />;

  const question = questions[currentIndex];
  const selectedOption = answers.get(currentIndex);
  const isAnswered = selectedOption !== undefined;
  const scorePercent = questions.length > 0 ? (score / questions.length) * 100 : 0;
  const timerActive = withTimer && !isAnswered && !allAnswered;

  return (
    <GlassCard variant="elevated" className="space-y-4">
      {/* Screen-reader answer feedback */}
      <div role="status" aria-live="polite" className="sr-only">
        {liveMessage}
      </div>

      {/* Header: progress + streak + timer */}
      <div className="flex items-center justify-between gap-2">
        <div className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-1.5 rounded-full bg-[color:var(--vie-accent)]"
          />
          <span className="text-xs font-medium tabular-nums tracking-wide text-muted-foreground/70">
            {`Q ${currentIndex + 1} of ${questions.length}`}
          </span>
          {bestScore != null && (
            <span className="ms-2 text-[10px] text-muted-foreground/60 tabular-nums">
              best {bestScore}/{questions.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {streak >= 2 && (
            <Badge
              variant="warning"
              className={cn(
                'text-xs font-semibold tabular-nums inline-flex items-center gap-1',
                pulseStreak && !reducedMotion && 'animate-[scalePulseOnce_0.4s_ease-out_both]',
              )}
            >
              <EmojiMarker emoji="🔥" size="sm" animated={false} />
              {streak} streak!
            </Badge>
          )}
          {answers.size > 0 && <InlineScore correct={score} total={answers.size} />}
          {withTimer && (
            <CountdownRing
              key={currentIndex}
              seconds={timerSeconds}
              active={timerActive}
              onExpire={handleTimerExpire}
            />
          )}
        </div>
      </div>

      {/* Scenario context (italic blockquote) */}
      {question.context && (
        <blockquote
          className={cn(
            'border-s-2 border-border/40 ps-3 italic text-sm leading-snug',
            'text-muted-foreground/90',
          )}
        >
          {question.context}
        </blockquote>
      )}

      {/* Frame evidence */}
      {(question.thumbnailUrl || question.frameCaption) && (
        <VisualEvidence
          variant="compact"
          thumbnailUrl={question.thumbnailUrl}
          caption={question.frameCaption}
          timestamp={question.timestamp}
          onSeek={onSeek}
        />
      )}

      {/* Question */}
      <FadeIn key={currentIndex}>
        <div className="text-center px-2">
          <p className="font-semibold text-base leading-snug tracking-tight">
            {question.question}
          </p>
        </div>
      </FadeIn>

      {/* Options */}
      <Shake active={shaking}>
        <OptionGrid
          options={question.options}
          selectedIndex={selectedOption != null && selectedOption >= 0 ? selectedOption : undefined}
          correctIndex={isAnswered ? question.correctIndex : undefined}
          onSelect={handleSelect}
        />
      </Shake>

      {/* Explanation */}
      {isAnswered && question.explanation && (
        <FadeIn>
          <div className="bg-muted/20 rounded-lg p-3">
            <span className="font-semibold text-xs uppercase tracking-wider text-muted-foreground block mb-1">
              Explanation
            </span>
            <QuizExplanation
              text={question.explanation}
              timestamp={question.timestamp}
              quote={question.quote}
              quoteTranslated={question.quoteTranslated}
              onSeek={onSeek}
            />
          </div>
        </FadeIn>
      )}

      {/* Navigation */}
      {questions.length > 1 && (
        <BackForward
          onBack={() => setCurrentIndex((p) => Math.max(0, p - 1))}
          onForward={() => setCurrentIndex((p) => Math.min(questions.length - 1, p + 1))}
          backDisabled={currentIndex === 0}
          forwardDisabled={currentIndex === questions.length - 1}
          backLabel={t.previous}
          forwardLabel={t.next}
        />
      )}

      {/* Stepper */}
      {questions.length > 1 && (
        <Stepper
          total={questions.length}
          current={currentIndex}
          completedSteps={answeredSteps}
          onStepClick={setCurrentIndex}
        />
      )}

      {/* Completion */}
      {allAnswered && (
        <div className="flex flex-col items-center gap-4">
          <ScoreRing score={score} total={questions.length} label={t.correct} size="md" />

          {scorePercent >= 80 && (
            <Celebration
              emoji="🧠"
              title={score === questions.length ? 'Perfect score!' : 'Great job!'}
              subtitle={`${score} of ${questions.length} correct${maxStreak >= 3 ? ` | Best streak: ${maxStreak}` : ''}`}
              nextTabId={nextTab}
              nextLabel={nextTab ? 'Review material' : undefined}
              onNavigateTab={onNavigateTab}
            />
          )}

          {wrongAnswers.length > 0 && (
            <FadeIn>
              <GlassCard variant="outlined" className="space-y-3 w-full">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t.review} <span className="tabular-nums">({wrongAnswers.length})</span> {t.missed}
                </h4>
                <ul className="space-y-2">
                  {wrongAnswers.map((w) => (
                    <li key={w.index} className="space-y-1">
                      <p className="font-semibold text-sm leading-snug">{w.question}</p>
                      <p className="text-destructive text-xs leading-relaxed">
                        Your answer: {w.picked}
                      </p>
                      <p className="text-success text-xs leading-relaxed">
                        Correct: {w.correct}
                      </p>
                    </li>
                  ))}
                </ul>
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full text-xs font-medium"
                  onClick={handleReset}
                >
                  {t.tryAgain}
                </Button>
              </GlassCard>
            </FadeIn>
          )}

          {wrongAnswers.length === 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="text-xs font-medium"
              onClick={handleReset}
            >
              {t.tryAgain}
            </Button>
          )}
        </div>
      )}
    </GlassCard>
  );
});
