import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, Clock, LayoutGrid, List } from 'lucide-react';

import { Lightbox } from '@/components/vie';
import { useTabState } from '@/features/video-output/contexts/TabStateContext';
import { useFocusBand } from '@/features/video-output/hooks/use-focus-band';
import { cn } from '@/lib/utils';
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion';

import { getMoodColor } from '../../../lib/moodColors';
import { EmptyTabState } from './EmptyTabState';
import { MomentGalleryCard } from './MomentGalleryCard';
import { MomentTimelineRow } from './MomentTimelineRow';
import { isClip, resolveLabel } from './moment-utils';
import type { MomentItem } from './moment-utils';

type TypeFilter = 'all' | 'clips' | 'moments';
type MomentView = 'grid' | 'timeline';

/** Share of items that must carry a description for the timeline view to open
 *  by default — the grid card clamps descriptions to one line, the timeline
 *  row shows them in full (R7: moments carry substance). */
const TIMELINE_DEFAULT_DESCRIPTION_SHARE = 0.5;

// Re-exported for existing consumers (tests, interactive/index.ts); the
// canonical home is the leaf module so the row/card children never import
// back into this orchestrator.
export { formatDuration, isClip, resolveLabel } from './moment-utils';
export type { MomentItem };

interface MomentTrackProps {
  items: MomentItem[];
  filters?: boolean;
  onSeek?: (seconds: number) => void;
  currentTime?: number;
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
}

/** A description the timeline row will actually show — the row hides one that
 *  merely repeats the label. */
function hasDescription(item: MomentItem): boolean {
  const description = item.description?.trim();
  return Boolean(description) && description !== resolveLabel(item);
}

/** Content-driven default: timeline when the items have descriptions to show. */
function defaultViewFor(items: MomentItem[]): MomentView {
  if (items.length === 0) return 'grid';
  const described = items.filter(hasDescription).length;
  return described / items.length >= TIMELINE_DEFAULT_DESCRIPTION_SHARE ? 'timeline' : 'grid';
}

export const MomentTrack = memo(function MomentTrack({
  items,
  filters,
  onSeek,
  currentTime,
  nextTab: _nextTab,
  onNavigateTab: _onNavigateTab,
}: MomentTrackProps) {
  // An explicit toggle choice wins for the life of this tab; otherwise the
  // view is derived from the items on every render, so a tab whose items land
  // later (provisional outline → final moments) still opens in the right
  // view. The choice is never persisted: the output tree remounts per video
  // (`key={videoSummaryId}`), so every video opens in its own content-driven
  // default instead of whatever the viewer last toggled elsewhere.
  const [chosenView, setChosenView] = useState<MomentView | null>(null);
  const view: MomentView = chosenView ?? defaultViewFor(items);
  const [moodFilter, setMoodFilter] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [pulseKey, setPulseKey] = useState<number>(-1);
  // Index of the row whose share link was just copied — drives the ~1.5s
  // "Link copied" confirmation (mirrors InfoGridInteractive's bulk-copy).
  const [copiedIndex, setCopiedIndex] = useState<number>(-1);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => { if (copyTimerRef.current) clearTimeout(copyTimerRef.current); }, []);
  const reducedMotion = usePrefersReducedMotion();
  const tabState = useTabState();
  const completedStepCount = tabState.completedSteps.size;

  const prevActiveRef = useRef<number>(-1);

  const hasClips = useMemo(() => items.some(isClip), [items]);
  const hasMoments = useMemo(() => items.some((i) => !isClip(i)), [items]);
  // Only `podcast.qa` moments carry a question: one is enough to read the whole
  // track as exchanges, so a stored interview never mixes two row layouts.
  const isExchangeTrack = useMemo(() => items.some((i) => Boolean(i.question?.trim())), [items]);
  const showTypeFilter = hasClips && hasMoments;

  const activeIndex = useMemo(() => {
    if (currentTime == null) return -1;
    // Don't assume `items` is chronologically sorted. Track the latest start
    // time we've seen so unsorted input still resolves the right active index.
    let best = -1;
    let bestSeconds = -Infinity;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const end = item.endSeconds ?? item.seconds + 0.5;
      if (currentTime >= item.seconds && currentTime < end) return i;
      if (item.seconds <= currentTime && item.seconds > bestSeconds) {
        best = i;
        bestSeconds = item.seconds;
      }
    }
    return best;
  }, [items, currentTime]);

  useEffect(() => {
    if (reducedMotion) return;
    if (activeIndex !== prevActiveRef.current && activeIndex >= 0) {
      setPulseKey(activeIndex);
      const timer = setTimeout(() => setPulseKey(-1), 650);
      prevActiveRef.current = activeIndex;
      return () => clearTimeout(timer);
    }
    prevActiveRef.current = activeIndex;
  }, [activeIndex, reducedMotion]);

  const uniqueMoods = useMemo(() => {
    const moods = new Set<string>();
    for (const item of items) {
      if (item.mood) moods.add(item.mood);
    }
    return Array.from(moods);
  }, [items]);

  // Carry originalIndex through filtering so the render loop doesn't pay an
  // O(N) `items.indexOf(item)` per row — was O(N²) per render on long videos.
  const filtered = useMemo(() => {
    const result: Array<{ item: MomentItem; originalIndex: number }> = [];
    items.forEach((item, originalIndex) => {
      if (moodFilter && item.mood !== moodFilter) return;
      if (typeFilter === 'clips' && !isClip(item)) return;
      if (typeFilter === 'moments' && isClip(item)) return;
      result.push({ item, originalIndex });
    });
    return result;
  }, [items, moodFilter, typeFilter]);

  const totalDuration = useMemo(() => {
    if (items.length === 0) return 0;
    // Don't assume the last item is the latest — scan for the maximum end time
    // so capsule-height ratios stay correct even when `items` is unsorted.
    let max = 0;
    for (const item of items) {
      const end = Math.max(item.endSeconds ?? item.seconds, item.seconds);
      if (end > max) max = end;
    }
    return max || 1;
  }, [items]);

  // Scroll focus band (timeline only). Playback position OVERRIDES scroll
  // focus: while an active row exists the observer is torn down and the
  // active row holds the grown state instead.
  const focusBandEnabled =
    view === 'timeline' && !reducedMotion && activeIndex < 0 && filtered.length >= 3;
  const { setRef } = useFocusBand({ enabled: focusBandEnabled, count: filtered.length });

  const handleViewChange = (next: MomentView): void => {
    setChosenView(next);
  };

  // Lightbox over the image-backed moments (viewing order = filtered order).
  // Indexed into `lightboxFrames`, not `items` — frameless moments are
  // skipped so arrow navigation never lands on an empty slide.
  const [lightboxIndex, setLightboxIndex] = useState(-1);
  const lightboxable = useMemo(
    () => filtered.filter(({ item }) => Boolean(item.thumbnailUrl)),
    [filtered],
  );
  const lightboxFrames = useMemo(
    () =>
      lightboxable.map(({ item }) => ({
        imageUrl: item.thumbnailUrl as string,
        caption: `${resolveLabel(item)} · ${item.time}`,
      })),
    [lightboxable],
  );
  // Stable callbacks: rows are memo()-ed and the player polls currentTime at
  // 1 Hz while open — fresh closures each tick would re-render every row.
  const handleExpand = useCallback(
    (originalIndex: number): void => {
      const idx = lightboxable.findIndex((entry) => entry.originalIndex === originalIndex);
      if (idx >= 0) setLightboxIndex(idx);
    },
    [lightboxable],
  );

  const handleShare = useCallback((item: MomentItem, index: number): void => {
    // Insecure origins (plain-http LAN/dev hosts) have no clipboard API — the
    // access itself throws synchronously, which a .catch() can't intercept.
    if (!navigator.clipboard?.writeText) return;
    const url = new URL(window.location.href);
    const hash = isClip(item) ? `t=${item.seconds},${item.endSeconds}` : `t=${item.seconds}`;
    url.hash = hash;
    navigator.clipboard
      .writeText(url.toString())
      .then(() => {
        setCopiedIndex(index);
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopiedIndex(-1), 1500);
      })
      .catch(() => {
        // Clipboard denied — the row simply doesn't flash "Link copied".
      });
  }, []);

  if (items.length === 0)
    return <EmptyTabState message="No moments were extracted for this video." icon={Clock} />;

  return (
    <div className="space-y-4" data-testid="moment-track" data-view={view}>
      {/* View toggle + filter row */}
      <div className="flex flex-wrap items-center gap-2">
        <div
          role="group"
          aria-label="View"
          className="inline-flex items-center rounded-full border border-border/50 bg-muted/20 p-0.5 text-xs font-medium"
        >
          {([
            { id: 'grid', label: 'Grid', Icon: LayoutGrid },
            { id: 'timeline', label: 'Timeline', Icon: List },
          ] as const).map(({ id, label, Icon }) => {
            const active = view === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => handleViewChange(id)}
                aria-pressed={active}
                data-testid={`view-${id}`}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full px-3 py-1 transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--vie-accent)]/60',
                  active
                    ? 'bg-[var(--vie-accent)] text-[var(--vie-accent-foreground)] shadow-sm'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {label}
              </button>
            );
          })}
        </div>

        {filters !== false && showTypeFilter && (
          <div
            role="group"
            aria-label="Filter by type"
            className="inline-flex items-center rounded-full border border-border/50 bg-muted/20 p-0.5 text-xs font-medium"
          >
            {([
              { id: 'all', label: 'All' },
              { id: 'clips', label: 'Clips' },
              { id: 'moments', label: 'Moments' },
            ] as const).map((opt) => {
              const active = typeFilter === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setTypeFilter(opt.id)}
                  aria-pressed={active}
                  data-testid={`type-filter-${opt.id}`}
                  className={cn(
                    'rounded-full px-3 py-1 transition-colors',
                    active ? 'bg-[var(--vie-accent)] text-[var(--vie-accent-foreground)] shadow-sm' : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        )}

        {filters !== false && uniqueMoods.length > 1 && (
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => setMoodFilter(null)}
              className={cn(
                'rounded-full px-2.5 py-1 text-xs font-medium border transition-colors',
                moodFilter === null
                  ? 'bg-[var(--vie-accent)] text-[var(--vie-accent-foreground)] border-[var(--vie-accent)]'
                  : 'bg-muted/20 text-muted-foreground border-border/50 hover:bg-muted/40',
              )}
            >
              All moods
            </button>
            {uniqueMoods.map((mood) => {
              const active = moodFilter === mood;
              return (
                <button
                  key={mood}
                  type="button"
                  onClick={() => setMoodFilter(active ? null : mood)}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium border transition-colors capitalize',
                    active
                      ? 'bg-[var(--vie-accent)] text-[var(--vie-accent-foreground)] border-[var(--vie-accent)]'
                      : 'bg-muted/20 text-muted-foreground border-border/50 hover:bg-muted/40',
                  )}
                >
                  <span
                    aria-hidden="true"
                    className="inline-block size-1.5 rounded-full shrink-0"
                    style={{ background: active ? 'currentColor' : getMoodColor(mood) }}
                  />
                  {mood}
                </button>
              );
            })}
          </div>
        )}

        {filters !== false && completedStepCount > 0 && (
          <div className="ms-auto flex items-center gap-1.5 px-1 text-xs font-medium text-success">
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
            <span>
              <span className="tabular-nums font-semibold">{completedStepCount}</span> step
              {completedStepCount !== 1 ? 's' : ''} completed
            </span>
          </div>
        )}
      </div>

      {view === 'grid' ? (
        <div
          data-testid="moment-grid"
          className="grid grid-cols-2 gap-3 sm:[grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]"
        >
          {filtered.map(({ item, originalIndex }, index) => (
            <MomentGalleryCard
              key={originalIndex}
              item={item}
              originalIndex={originalIndex}
              index={index}
              isActive={activeIndex === originalIndex}
              copied={copiedIndex === originalIndex}
              currentTime={activeIndex === originalIndex ? currentTime : undefined}
              onSeek={onSeek}
              onExpand={handleExpand}
              onShare={handleShare}
            />
          ))}
        </div>
      ) : (
        <>
          {/* Track — the decorative spine sits OUTSIDE the <ol> so every <li>
              stays a direct child of the list (valid <ol> content model). */}
          <div className="relative">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute start-[14px] top-1 bottom-1 w-px bg-gradient-to-b from-[var(--vie-accent)]/50 via-[var(--vie-accent)]/25 to-transparent"
            />
            <ol className="space-y-2 [overflow-anchor:auto]" aria-label="Moment track">
              {filtered.map(({ item, originalIndex }, index) => (
                <MomentTimelineRow
                  key={originalIndex}
                  item={item}
                  originalIndex={originalIndex}
                  isExchange={isExchangeTrack}
                  index={index}
                  isActive={activeIndex === originalIndex}
                  pulsing={pulseKey === originalIndex && !reducedMotion}
                  copied={copiedIndex === originalIndex}
                  totalDuration={totalDuration}
                  currentTime={activeIndex === originalIndex ? currentTime : undefined}
                  onSeek={onSeek}
                  onExpand={handleExpand}
                  onShare={handleShare}
                  setFocusRef={focusBandEnabled ? setRef : undefined}
                />
              ))}
            </ol>
          </div>
          {/* Trailing room so the last rows can still reach the focus band. */}
          {focusBandEnabled && <div aria-hidden="true" className="h-[30vh]" />}
        </>
      )}

      {lightboxIndex >= 0 && lightboxFrames[lightboxIndex] && (
        <Lightbox
          frames={lightboxFrames}
          activeIndex={lightboxIndex}
          onClose={() => setLightboxIndex(-1)}
          onNavigate={setLightboxIndex}
        />
      )}
    </div>
  );
});
