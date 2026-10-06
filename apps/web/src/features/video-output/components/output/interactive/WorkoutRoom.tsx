import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Check,
  Circle,
  CircleCheck,
  Dumbbell,
  Volume2,
  VolumeX,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import {
  GlassCard,
  HeroCard,
  Badge,
  BackForward,
  Timer,
  VisualEvidence,
  TextBlock,
} from '@/components/vie';
import { Celebration } from '../Celebration';
import { EmptyTabState } from './EmptyTabState';
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion';

import type { FitnessExercise } from '@vie/types';

// Local extension: the canonical FitnessExercise type doesn't carry a frame
// thumbnail today, but assemblers may attach one for form-cue surfacing.
interface WorkoutExercise extends FitnessExercise {
  thumbnailUrl?: string;
  frameCaption?: string;
}

interface WorkoutRoomProps {
  exercises: WorkoutExercise[];
  warmup?: WorkoutExercise[];
  cooldown?: WorkoutExercise[];
  onSeek?: (seconds: number) => void;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

const REST_SECONDS = 3;
const AUDIO_CUE_FREQ_HZ = 880;
const AUDIO_CUE_MS = 100;

function parseDurationSeconds(duration: string | undefined): number | null {
  if (!duration) return null;
  // Matches "45s", "1m", "1:30", "45 seconds", "1 min".
  const colonMatch = duration.match(/^(\d+):(\d{1,2})$/);
  if (colonMatch) {
    return parseInt(colonMatch[1], 10) * 60 + parseInt(colonMatch[2], 10);
  }
  const minMatch = duration.match(/(\d+)\s*m(?:in|ins|inute|inutes)?\b/i);
  if (minMatch) return parseInt(minMatch[1], 10) * 60;
  const secMatch = duration.match(/(\d+)\s*s(?:ec|ecs|econd|econds)?\b/i);
  if (secMatch) return parseInt(secMatch[1], 10);
  const bare = duration.match(/^(\d+)$/);
  if (bare) return parseInt(bare[1], 10);
  return null;
}

interface PlayCueOptions {
  enabled: boolean;
}

function playAudioCue({ enabled }: PlayCueOptions): void {
  if (!enabled) return;
  if (typeof window === 'undefined') return;
  try {
    const AudioCtor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AudioCtor) return;
    const ctx = new AudioCtor();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = AUDIO_CUE_FREQ_HZ;
    osc.type = 'sine';
    gain.gain.value = 0.05;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + AUDIO_CUE_MS / 1000);
    // Tear down the context after the cue so we don't leak audio graphs.
    window.setTimeout(() => {
      void ctx.close().catch(() => undefined);
    }, AUDIO_CUE_MS + 50);
  } catch {
    // Audio is opt-in; failure should never break the workout.
  }
}

interface SetIndicatorProps {
  done: number;
  total: number;
}

function SetIndicator({ done, total }: SetIndicatorProps) {
  return (
    <div
      className="flex items-center gap-1"
      role="img"
      aria-label={`${done} of ${total} sets complete`}
    >
      {Array.from({ length: total }, (_, i) =>
        i < done ? (
          <CircleCheck
            key={i}
            className="h-4 w-4 text-[var(--vie-accent)]"
            aria-hidden="true"
          />
        ) : (
          <Circle
            key={i}
            className="h-4 w-4 text-muted-foreground/40"
            aria-hidden="true"
          />
        ),
      )}
    </div>
  );
}

export const WorkoutRoom = memo(function WorkoutRoom({
  exercises,
  onSeek,
  nextTab,
  onNavigateTab,
}: WorkoutRoomProps) {
  const t = useLabels();
  const reducedMotion = usePrefersReducedMotion();
  const [activeIndex, setActiveIndex] = useState(0);
  const [completedSets, setCompletedSets] = useState<number[]>(() =>
    exercises.map(() => 0),
  );
  const [restRemaining, setRestRemaining] = useState<number | null>(null);
  const [reps, setReps] = useState<number>(0);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [framePulse, setFramePulse] = useState(false);

  const restTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const restAdvanceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pulseTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Derived-state resets done during render (React "adjusting state on prop
  // change" pattern) instead of effects, so no cascading post-commit render.
  const [prevExerciseCount, setPrevExerciseCount] = useState(exercises.length);
  if (prevExerciseCount !== exercises.length) {
    setPrevExerciseCount(exercises.length);
    setCompletedSets((prev) => exercises.map((_, i) => prev[i] ?? 0));
  }
  const [prevActiveIndex, setPrevActiveIndex] = useState(activeIndex);
  if (prevActiveIndex !== activeIndex) {
    setPrevActiveIndex(activeIndex);
    setReps(0);
  }

  const clearRestTimers = useCallback(() => {
    if (restTimerRef.current) {
      clearInterval(restTimerRef.current);
      restTimerRef.current = null;
    }
    if (restAdvanceRef.current) {
      clearTimeout(restAdvanceRef.current);
      restAdvanceRef.current = null;
    }
  }, []);

  // Form-cue frame loop: pulse opacity 1.5s when not under reduced motion.
  // Render already gates the pulse class on !reducedMotion, so no reset needed.
  useEffect(() => {
    if (reducedMotion) return;
    pulseTimerRef.current = setInterval(() => {
      setFramePulse((p) => !p);
    }, 1500);
    return () => {
      if (pulseTimerRef.current) {
        clearInterval(pulseTimerRef.current);
        pulseTimerRef.current = null;
      }
    };
  }, [reducedMotion]);

  useEffect(
    () => () => {
      clearRestTimers();
      if (pulseTimerRef.current) clearInterval(pulseTimerRef.current);
    },
    [clearRestTimers],
  );

  const totalSets = useMemo(
    () => exercises.reduce((s, ex) => s + (ex.sets ?? 1), 0),
    [exercises],
  );
  const doneSets = useMemo(
    () => completedSets.reduce((s, n) => s + n, 0),
    [completedSets],
  );
  const allDone = doneSets >= totalSets && totalSets > 0;

  const startAutoAdvance = useCallback(
    (fromIndex: number) => {
      clearRestTimers();
      setRestRemaining(REST_SECONDS);
      restTimerRef.current = setInterval(() => {
        setRestRemaining((prev) =>
          prev === null || prev <= 1 ? 0 : prev - 1,
        );
      }, 1000);
      restAdvanceRef.current = setTimeout(() => {
        clearRestTimers();
        setRestRemaining(null);
        // Audio cue marks end of rest.
        playAudioCue({ enabled: soundEnabled });
        setActiveIndex((idx) =>
          fromIndex === idx && idx < exercises.length - 1 ? idx + 1 : idx,
        );
      }, REST_SECONDS * 1000);
    },
    [clearRestTimers, exercises.length, soundEnabled],
  );

  const completeSet = useCallback(() => {
    const exercise = exercises[activeIndex];
    const target = exercise?.sets ?? 1;
    const current = completedSets[activeIndex] ?? 0;
    if (current >= target) return;
    const nextCount = current + 1;
    setCompletedSets((prev) => {
      const next = [...prev];
      next[activeIndex] = nextCount;
      return next;
    });
    if (nextCount >= target && activeIndex < exercises.length - 1) {
      startAutoAdvance(activeIndex);
    }
  }, [activeIndex, completedSets, exercises, startAutoAdvance]);

  const skipRest = useCallback(() => {
    clearRestTimers();
    setRestRemaining(null);
    setActiveIndex((idx) =>
      idx < exercises.length - 1 ? idx + 1 : idx,
    );
  }, [clearRestTimers, exercises.length]);

  const goBack = useCallback(() => {
    clearRestTimers();
    setRestRemaining(null);
    setActiveIndex((idx) => Math.max(0, idx - 1));
  }, [clearRestTimers]);

  const goForward = useCallback(() => {
    clearRestTimers();
    setRestRemaining(null);
    setActiveIndex((idx) => Math.min(exercises.length - 1, idx + 1));
  }, [clearRestTimers, exercises.length]);

  if (exercises.length === 0) return <EmptyTabState message="No exercises were extracted for this video." icon={Dumbbell} />;

  const exercise = exercises[activeIndex];
  const setsDone = completedSets[activeIndex] ?? 0;
  const setsTarget = exercise.sets ?? 1;
  const exerciseDone = setsDone >= setsTarget;
  const durationSeconds = parseDurationSeconds(exercise.duration);
  const repsAsNumber =
    typeof exercise.reps === 'string' ? parseInt(exercise.reps, 10) : null;
  const repsKnown = repsAsNumber !== null && !Number.isNaN(repsAsNumber);

  return (
    <div className="space-y-4">
      {/* Hero meta */}
      <HeroCard
        emoji={exercise.emoji ?? '🏋️'}
        title={exercise.name}
        subtitle={`Exercise ${activeIndex + 1} of ${exercises.length}`}
      >
        <div className="flex flex-wrap items-center gap-2 mt-2">
          {exercise.difficulty && (
            <Badge
              variant={
                exercise.difficulty === 'beginner'
                  ? 'success'
                  : exercise.difficulty === 'advanced'
                    ? 'destructive'
                    : 'warning'
              }
            >
              {exercise.difficulty}
            </Badge>
          )}
          {exercise.sets && (
            <Badge variant="muted">
              <span className="tabular-nums">{exercise.sets}</span>&nbsp;sets
            </Badge>
          )}
          {exercise.reps && (
            <Badge variant="muted">
              <span className="tabular-nums">{exercise.reps}</span>&nbsp;reps
            </Badge>
          )}
          {exercise.duration && <Badge variant="muted">{exercise.duration}</Badge>}
        </div>
      </HeroCard>

      {/* Total progress */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <Dumbbell className="h-3.5 w-3.5" aria-hidden="true" />
          Total progress
        </span>
        <span className="tabular-nums">
          {doneSets}/{totalSets} sets
        </span>
      </div>

      {/* Form-cue frame (figure variant, loops opacity unless reduced motion) */}
      {exercise.thumbnailUrl && (
        <div
          className={cn(
            'transition-opacity duration-[1500ms] ease-in-out',
            framePulse && !reducedMotion ? 'opacity-100' : 'opacity-80',
          )}
        >
          <VisualEvidence
            variant="figure"
            thumbnailUrl={exercise.thumbnailUrl}
            caption={exercise.frameCaption ?? `Form cue: ${exercise.name}`}
            sceneType="demo"
            timestamp={exercise.timestamp}
            onSeek={onSeek}
          />
        </div>
      )}

      {/* Form cues list */}
      {exercise.formCues && exercise.formCues.length > 0 && (
        <GlassCard variant="subtle" className="space-y-1.5">
          <h4 className="text-xs font-bold uppercase tracking-wider text-info">
            Form cues
          </h4>
          <ul className="space-y-1" role="list">
            {exercise.formCues.map((cue, i) => (
              <li
                key={i}
                className="flex items-start gap-2 text-xs text-muted-foreground"
              >
                <span
                  className="mt-1 inline-block h-1 w-1 rounded-full bg-info/70 shrink-0"
                  aria-hidden="true"
                />
                <span>{cue}</span>
              </li>
            ))}
          </ul>
        </GlassCard>
      )}

      {/* Controls: duration timer OR reps input */}
      <GlassCard className="space-y-4">
        <div className="flex items-center justify-between">
          <SetIndicator done={setsDone} total={setsTarget} />
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setSoundEnabled((s) => !s)}
              aria-label={soundEnabled ? t.muteAudioCues : t.enableAudioCues}
              aria-pressed={soundEnabled}
              className="gap-1 text-xs"
            >
              {soundEnabled ? (
                <Volume2 className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <VolumeX className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {soundEnabled ? t.soundOn : t.soundOff}
            </Button>
          </div>
        </div>

        {durationSeconds !== null ? (
          <div className="flex justify-center">
            {/* Timer seeds its countdown once; a per-exercise key restarts it for the next exercise. */}
            <Timer key={activeIndex} duration={durationSeconds} />
          </div>
        ) : repsKnown ? (
          <div className="flex items-center justify-center gap-3">
            <label
              htmlFor="workout-room-reps"
              className="text-xs uppercase tracking-wider text-muted-foreground"
            >
              Reps
            </label>
            <input
              id="workout-room-reps"
              type="number"
              inputMode="numeric"
              min={0}
              value={reps}
              onChange={(e) => setReps(Math.max(0, Number(e.target.value)))}
              className={cn(
                'w-20 rounded-md border border-border/40 bg-card/60 px-2 py-1.5',
                'text-center text-sm tabular-nums font-medium',
                'focus:outline-none focus:ring-2 focus:ring-[var(--vie-accent)]/40',
              )}
            />
            <span className="text-xs text-muted-foreground tabular-nums">
              / {repsAsNumber}
            </span>
          </div>
        ) : null}

        {restRemaining !== null ? (
          <div
            className="rounded-lg bg-info/10 border border-info/20 px-4 py-3 text-center"
            role="status"
            aria-live="polite"
          >
            <p className="text-xs uppercase tracking-wider text-info">
              Resting
            </p>
            <p className="text-2xl font-mono font-bold tabular-nums text-info mt-1">
              {restRemaining}s
            </p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={skipRest}
              className="text-xs mt-1"
            >
              {t.skipRest}
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            variant={exerciseDone ? 'ghost' : 'default'}
            size="default"
            onClick={completeSet}
            disabled={exerciseDone}
            className="w-full gap-2"
          >
            <Check className="h-4 w-4" aria-hidden="true" />
            {exerciseDone ? t.exerciseComplete : t.completeSet}
          </Button>
        )}
      </GlassCard>

      {/* Modifications */}
      {exercise.modifications && exercise.modifications.length > 0 && (
        <div className="rounded-xl border border-border/40 bg-card/40 px-4 py-3 space-y-2">
          <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
            Modifications ({exercise.modifications.length})
          </p>
          <ul className="space-y-2" role="list">
            {exercise.modifications.map((mod, i) => (
              <li key={i} className="text-xs">
                <p className="font-medium text-foreground">{mod.label}</p>
                <p className="text-muted-foreground mt-0.5">
                  {mod.description}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Supersets hint */}
      {exercise.supersetWith && (
        <TextBlock intent="note">
          Pairs with: <span className="font-medium">{exercise.supersetWith}</span>
        </TextBlock>
      )}

      {/* Nav */}
      <BackForward
        onBack={goBack}
        onForward={goForward}
        backDisabled={activeIndex === 0}
        forwardDisabled={activeIndex >= exercises.length - 1}
      />

      {allDone && (
        <Celebration
          emoji="💪"
          title="Workout complete"
          subtitle={`All ${totalSets} sets done.`}
          nextTabId={nextTab}
          nextLabel={nextTab ? 'Continue' : undefined}
          onNavigateTab={onNavigateTab}
        />
      )}
    </div>
  );
});
