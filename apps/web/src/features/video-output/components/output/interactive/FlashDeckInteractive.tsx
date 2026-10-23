import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Shuffle, Layers } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import type { FlashcardItem } from '@vie/types';
import { GlassCard, FadeIn, BackForward, Stepper, Badge, ProgressBar, EmojiMarker } from '@/components/vie';
import { Celebration } from '../Celebration';
import { EmptyTabState } from './EmptyTabState';
import { useLabels } from '@/lib/i18n';
import { useDirection } from '@/contexts/DirectionContext';

import { useTabState } from '@/features/video-output/contexts/TabStateContext';
import { useTabCoordination } from '../TabCoordinationContext';

interface FlashDeckInteractiveProps {
  cards: FlashcardItem[];
  shuffleable?: boolean;
  tabId?: string;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

export const FlashDeckInteractive = memo(function FlashDeckInteractive({
  cards: initialCards,
  shuffleable,
  tabId = 'flashcards',
  nextTab,
  onNavigateTab,
}: FlashDeckInteractiveProps) {
  const t = useLabels();
  const { isRTL } = useDirection();
  const tabState = useTabState();
  const tabCoord = useTabCoordination();
  const [shuffleIndices, setShuffleIndices] = useState<number[] | null>(null);
  const cards = useMemo(
    () => shuffleIndices ? shuffleIndices.map((i) => initialCards[i]).filter(Boolean) : initialCards,
    [initialCards, shuffleIndices],
  );
  const [currentCard, setCurrentCard] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [known, setKnown] = useState<Set<number>>(new Set());
  const [reviewed, setReviewed] = useState<Set<number>>(new Set());

  const handleFlip = useCallback(() => {
    setFlipped((prev) => {
      if (!prev) setReviewed((s) => new Set(s).add(currentCard));
      return !prev;
    });
  }, [currentCard]);

  const goTo = useCallback((index: number) => {
    setFlipped(false);
    setCurrentCard(index);
  }, []);

  const markKnown = useCallback(() => {
    setKnown((prev) => new Set(prev).add(currentCard));
    setReviewed((prev) => new Set(prev).add(currentCard));
    tabState.masterCard(String(currentCard));
    // Auto-advance to next unreviewed
    const nextUnreviewed = cards.findIndex((_, i) => i > currentCard && !known.has(i));
    if (nextUnreviewed !== -1) goTo(nextUnreviewed);
    else if (currentCard < cards.length - 1) goTo(currentCard + 1);
  }, [currentCard, cards, known, goTo, tabState]);

  const markReview = useCallback(() => {
    setKnown((prev) => {
      const next = new Set(prev);
      next.delete(currentCard);
      return next;
    });
    setReviewed((prev) => new Set(prev).add(currentCard));
    if (currentCard < cards.length - 1) goTo(currentCard + 1);
  }, [currentCard, cards.length, goTo]);

  const handleShuffle = useCallback(() => {
    const indices = initialCards.map((_, i) => i);
    // Fisher-Yates shuffle for uniform distribution
    for (let i = indices.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [indices[i], indices[j]] = [indices[j], indices[i]];
    }
    setShuffleIndices(indices);
    setCurrentCard(0);
    setFlipped(false);
    setKnown(new Set());
    setReviewed(new Set());
  }, [initialCards]);

  const allReviewed = cards.length > 0 && reviewed.size === cards.length;

  // Mark tab completed when all cards reviewed
  useEffect(() => {
    if (allReviewed) tabCoord?.markTabCompleted(tabId);
  }, [allReviewed, tabCoord, tabId]);

  // Read quiz results for review flags
  const cardNeedsReview = useMemo(() => {
    if (tabState.quizResults.size === 0) return new Set<number>();
    const needsReview = new Set<number>();
    // Match card indices to quiz results by index (simplified matching)
    tabState.quizResults.forEach((correct, qIdx) => {
      if (!correct) {
        const idx = parseInt(qIdx, 10);
        if (!isNaN(idx) && idx < cards.length) needsReview.add(idx);
      }
    });
    return needsReview;
  }, [tabState.quizResults, cards.length]);

  // Keyboard shortcuts. Arrow keys map to visual direction: in LTR the
  // "forward" key is ArrowRight, in RTL it's ArrowLeft. The card behavior
  // stays the same (advance == "got it", back == "review again") — only the
  // physical key that triggers each action flips.
  useEffect(() => {
    const forwardKey = isRTL ? 'ArrowLeft' : 'ArrowRight';
    const backKey = isRTL ? 'ArrowRight' : 'ArrowLeft';
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        handleFlip();
      } else if (e.key === forwardKey && flipped) {
        markKnown();
      } else if (e.key === backKey && flipped) {
        markReview();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handleFlip, flipped, markKnown, markReview, isRTL]);

  // Touch swipe handling
  const touchStartX = useRef<number | null>(null);
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
  }, []);
  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    if (touchStartX.current == null) return;
    const delta = e.changedTouches[0].clientX - touchStartX.current;
    touchStartX.current = null;
    const SWIPE_THRESHOLD = 50;
    if (Math.abs(delta) < SWIPE_THRESHOLD) return;
    // In RTL, forward motion is a swipe to the LEFT (negative delta). Mirror
    // the mapping so swipe semantics match the reading direction.
    const forward = isRTL ? delta < 0 : delta > 0;
    if (forward) {
      if (flipped) markKnown();
      else handleFlip();
    } else {
      if (flipped) markReview();
      else handleFlip();
    }
  }, [flipped, handleFlip, markKnown, markReview, isRTL]);

  if (cards.length === 0) return <EmptyTabState message="No flashcards were extracted for this video." icon={Layers} />;

  const card = cards[currentCard];

  return (
    <GlassCard className="space-y-4">
      {/* Header with counter + shuffle */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium tabular-nums tracking-wide text-muted-foreground/70">
          {currentCard + 1} of {cards.length}
        </span>
        <div className="flex items-center gap-2">
          {reviewed.size > 0 && (
            <span className="text-xs font-medium tabular-nums text-muted-foreground">
              {known.size} known / {reviewed.size - known.size} review
            </span>
          )}
          {shuffleable && (
            <Button variant="ghost" size="icon" onClick={handleShuffle} aria-label="Shuffle cards">
              <Shuffle className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Progress bar showing reviewed/total */}
      <ProgressBar value={reviewed.size} max={cards.length} label={t.cardsReviewed} />

      {/* 3D flip card — physical deck feel with 2 background cards */}
      <FadeIn key={currentCard}>
        <div className="relative" style={{ minHeight: '180px' }}>
          {/* Behind card 2 (furthest) */}
          <div
            aria-hidden="true"
            className="absolute inset-0 rounded-2xl border border-border/40 bg-muted/10 scale-[0.92] translate-y-4 opacity-30 -z-20"
          />
          {/* Behind card 1 */}
          <div
            aria-hidden="true"
            className="absolute inset-0 rounded-2xl border border-border/50 bg-muted/15 scale-[0.96] translate-y-2 opacity-60 -z-10"
          />
          <div
            className="relative cursor-pointer transition-transform duration-200 hover:rotate-[0.5deg]"
            style={{ perspective: '1000px' }}
            onClick={handleFlip}
            onTouchStart={handleTouchStart}
            onTouchEnd={handleTouchEnd}
            role="button"
            tabIndex={0}
            aria-label={flipped ? card.back : card.front}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleFlip();
              }
            }}
          >
          <div
            className="relative w-full transition-transform duration-500"
            style={{
              transformStyle: 'preserve-3d',
              transform: flipped ? 'rotateY(180deg)' : 'rotateY(0deg)',
              minHeight: '180px',
            }}
          >
            {/* Front */}
            <div
              className={cn(
                'absolute inset-0 flex flex-col items-center justify-center',
                'rounded-2xl border border-border/50 bg-muted/20 p-6 text-center shadow-2xl',
              )}
              style={{ backfaceVisibility: 'hidden' }}
            >
              {card.category && (
                <Badge variant="muted" className="mb-2 text-xs font-semibold uppercase tracking-wider">
                  {card.category}
                </Badge>
              )}
              {card.emoji && <span className="text-3xl mb-3" aria-hidden="true">{card.emoji}</span>}
              <p dir="auto" className="font-semibold text-base leading-snug tracking-tight">{card.front}</p>
              {cardNeedsReview.has(currentCard) && (
                <Badge variant="warning" className="mt-2 text-xs font-medium inline-flex items-center gap-1">
                  <EmojiMarker emoji="🔄" size="sm" animated={false} />
                  Missed in quiz
                </Badge>
              )}
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground/50 mt-3 inline-flex items-center gap-1.5">
                Tap
                <kbd className="px-1.5 py-0.5 rounded bg-muted/60 border border-border/60 text-[10px] normal-case tracking-normal">Space</kbd>
                to flip
              </span>
            </div>

            {/* Back */}
            <div
              className={cn(
                'absolute inset-0 flex flex-col items-center justify-center',
                'rounded-2xl border border-[color:var(--vie-accent-border)] bg-[color:var(--vie-accent-muted)] p-6 text-center shadow-2xl',
              )}
              style={{ backfaceVisibility: 'hidden', transform: 'rotateY(180deg)' }}
            >
              {card.quoteEn && (
                <p lang="en" dir="ltr" className="mb-2 text-sm leading-relaxed text-foreground/85">{card.quoteEn}</p>
              )}
              <p dir="auto" className="text-sm leading-relaxed text-muted-foreground">{card.back}</p>
            </div>
          </div>
          </div>
        </div>
      </FadeIn>

      {/* Known / Review buttons (shown after flip) */}
      {flipped && !known.has(currentCard) && (
        <FadeIn>
          <div className="flex items-center gap-2 justify-center">
            <Button variant="outline" size="sm" onClick={markReview} className="text-xs font-medium gap-1.5">
              {t.reviewAgain}
            </Button>
            <Button variant="default" size="sm" onClick={markKnown} className="text-xs font-semibold gap-1.5">
              {t.gotIt}
            </Button>
          </div>
        </FadeIn>
      )}

      {/* Navigation */}
      {cards.length > 1 && (
        <BackForward
          onBack={() => goTo(currentCard - 1)}
          onForward={() => goTo(currentCard + 1)}
          backDisabled={currentCard === 0}
          forwardDisabled={currentCard === cards.length - 1}
          backLabel={t.previous}
          forwardLabel={t.next}
        />
      )}

      {/* Stepper dots */}
      {cards.length > 1 && (
        <Stepper
          total={cards.length}
          current={currentCard}
          completedSteps={known}
          onStepClick={goTo}
        />
      )}

      {/* Celebration */}
      {allReviewed && (
        <Celebration
          emoji="🃏"
          title={known.size === cards.length ? 'All mastered!' : 'All cards reviewed!'}
          subtitle={`${known.size} known, ${cards.length - known.size} to review`}
          nextTabId={nextTab}
          nextLabel={nextTab ? 'Continue' : undefined}
          onNavigateTab={onNavigateTab}
        />
      )}

    </GlassCard>
  );
});
