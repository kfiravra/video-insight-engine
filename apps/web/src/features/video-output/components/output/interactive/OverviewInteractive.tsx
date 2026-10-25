import { memo, useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { Star, ArrowRight, Download } from 'lucide-react';
import { cn } from '@/lib/utils';
import { FadeIn, GlassCard, ExpandableCard, Badge, QuoteBlock } from '@/components/vie';
import { useLabels } from '@/lib/i18n';


interface OverviewHighlight {
  emoji: string;
  text: string;
}

/** Sibling-tab nav entry rendered in the Overview's "Continue exploring" grid.
 *  Exported so the producer (ComposableOutput) imports a single source of truth
 *  and the shape can't drift between sites. */
export interface OverviewCrossTabLink {
  targetTab: string;
  label: string;
  count?: number;
  emoji?: string;
}

interface OverviewInteractiveProps {
  /** @deprecated Owned by the page-level VideoHero — passing has no effect.
   *  Retained in the type so the v2 backend assembler can keep emitting the
   *  field without TS errors at the call site. */
  title?: string;
  /** @deprecated Owned by the page-level VideoHero. */
  subtitle?: string;
  /** @deprecated Owned by the page-level VideoHero (Brief surface). */
  summary?: string;
  /** @deprecated Owned by the page-level VideoHero (Brief surface). */
  masterSummary?: string;

  // Rendered surfaces.
  highlights?: OverviewHighlight[];
  tips?: string[];
  keyTakeaways?: string[];
  quote?: string;
  quoteAuthor?: string;
  duration?: string;
  level?: string;
  itemCount?: number;
  crossTabLinks?: OverviewCrossTabLink[];
  videoId?: string;
  onNavigateTab?: (id: string) => void;
}

const STARRED_KEY = (id: string): string => `vie-overview-starred-${id}`;

export const OverviewInteractive = memo(function OverviewInteractive({
  highlights,
  tips,
  keyTakeaways,
  quote,
  quoteAuthor,
  duration,
  level,
  itemCount,
  crossTabLinks,
  videoId,
  onNavigateTab,
}: OverviewInteractiveProps) {
  const t = useLabels();
  // Lazy init from localStorage — keeps the hydration synchronous with the
  // first render so we don't flash an empty state, and sidesteps the
  // set-state-in-effect rule. The component is re-mounted per-video so we
  // don't need to react to videoId changes after mount.
  const [starredHighlights, setStarredHighlights] = useState<Set<number>>(() => {
    if (typeof window === 'undefined' || !videoId) return new Set();
    try {
      const saved = window.localStorage.getItem(STARRED_KEY(videoId));
      if (!saved) return new Set();
      const parsed: unknown = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.every((v) => typeof v === 'number')) {
        return new Set(parsed as number[]);
      }
    } catch { /* ignore corrupt data */ }
    return new Set();
  });

  // Top takeaways live in the page hero. Takeaway 4+ back the collapsible
  // below (when there's no curated highlights array) so overflow stays
  // surfaced — same UI, real content, no backend change needed. Curated
  // highlights win when present (richer than plain-text fallbacks).
  const extraTakeaways = useMemo(() => keyTakeaways?.slice(3) ?? [], [keyTakeaways]);
  const hasRealHighlights = !!(highlights && highlights.length > 0);
  const collapsibleItems = useMemo<OverviewHighlight[]>(() => {
    if (hasRealHighlights) return highlights!;
    return extraTakeaways.map((text) => ({ emoji: '•', text }));
  }, [hasRealHighlights, highlights, extraTakeaways]);
  const collapsibleLabel = hasRealHighlights ? t.highlights : t.moreTakeaways;

  // Inline metadata strip — replaces the inner HeroCard, surfaces only the
  // attributes the page hero doesn't already show prominently.
  const metaChips = useMemo<Array<{ label: string; value: string }>>(() => {
    const out: Array<{ label: string; value: string }> = [];
    if (level) out.push({ label: t.level, value: level });
    if (duration) out.push({ label: t.duration, value: duration });
    if (itemCount != null && itemCount > 0) out.push({ label: t.items, value: String(itemCount) });
    return out;
  }, [duration, level, itemCount, t.duration, t.level, t.items]);

  const toggleStar = useCallback((index: number) => {
    setStarredHighlights((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  // Debounce writes to localStorage so per-click churn collapses into one
  // serialize. The cleanup *flushes* the snapshot synchronously on unmount —
  // cancelling the timer would lose a star-then-navigate-away within 500ms.
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (!videoId) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    const flush = (): void => {
      try {
        if (starredHighlights.size === 0) {
          localStorage.removeItem(STARRED_KEY(videoId));
        } else {
          localStorage.setItem(STARRED_KEY(videoId), JSON.stringify(Array.from(starredHighlights)));
        }
      } catch { /* ignore quota / private-mode errors */ }
    };
    saveTimerRef.current = setTimeout(flush, 500);
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        saveTimerRef.current = undefined;
        flush();
      }
    };
  }, [videoId, starredHighlights]);

  const hasAnyContent =
    metaChips.length > 0 ||
    !!(crossTabLinks && crossTabLinks.length > 0) ||
    !!quote ||
    collapsibleItems.length > 0 ||
    !!(tips && tips.length > 0);

  if (!hasAnyContent) return null;

  return (
    <div className="space-y-4">
      {metaChips.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground px-1">
          {metaChips.map((chip, i) => (
            <span key={chip.label} className="inline-flex items-center gap-1">
              {i > 0 && <span aria-hidden="true" className="text-muted-foreground/40">·</span>}
              <span className="font-semibold text-foreground/85 tabular-nums">{chip.value}</span>
              <span className="text-muted-foreground/70">{chip.label}</span>
            </span>
          ))}
        </div>
      )}

      {crossTabLinks && crossTabLinks.length > 0 && onNavigateTab && (
        <FadeIn index={1}>
          <div className="space-y-2">
            <span className="type-eyebrow px-1 text-[color:var(--vie-accent)]">{t.continueExploring}</span>
            <div
              className="grid gap-2"
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}
            >
              {crossTabLinks.map((link) => (
                <button
                  key={link.targetTab}
                  type="button"
                  onClick={() => onNavigateTab(link.targetTab)}
                  className={cn(
                    'group relative flex items-center justify-between gap-3 overflow-hidden rounded-xl',
                    'border border-border/50 bg-muted/10 px-3.5 py-3 text-start',
                    'transition-all duration-200 ease-[var(--ease-out-expo)]',
                    'hover:-translate-y-0.5 hover:border-border hover:bg-muted/25 motion-reduce:hover:translate-y-0',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  )}
                  aria-label={`Open ${link.label}`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    {link.emoji && (
                      <span aria-hidden="true" className="text-base leading-none shrink-0">
                        {link.emoji}
                      </span>
                    )}
                    <span className="truncate text-sm font-semibold leading-tight text-foreground/90">
                      {link.label}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    {typeof link.count === 'number' && link.count > 0 && (
                      <Badge variant="muted" className="tabular-nums">{link.count}</Badge>
                    )}
                    <ArrowRight
                      className="h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 group-hover:translate-x-0.5 rtl:rotate-180 rtl:group-hover:-translate-x-0.5 motion-reduce:group-hover:translate-x-0"
                      aria-hidden="true"
                    />
                  </span>
                </button>
              ))}
            </div>
          </div>
        </FadeIn>
      )}

      {quote && (
        <FadeIn index={2}>
          <GlassCard variant="outlined">
            <QuoteBlock text={quote} attribution={quoteAuthor} variant="speaker" />
          </GlassCard>
        </FadeIn>
      )}

      {collapsibleItems.length > 0 && (
        <ExpandableCard
          defaultExpanded
          header={
            <div className="flex items-center gap-2 w-full">
              <span className="type-eyebrow">{collapsibleLabel}</span>
              <Badge variant="muted" className="tabular-nums">{collapsibleItems.length}</Badge>
              {videoId && starredHighlights.size > 0 && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    const starred = Array.from(starredHighlights)
                      .sort((a, b) => a - b)
                      .map((i) => collapsibleItems[i])
                      .filter(Boolean);
                    const text = starred
                      .map((h) => `${h.emoji} ${h.text}`)
                      .join('\n\n');
                    const headerLine = `# ${collapsibleLabel} — starred\n\n`;
                    const blob = new Blob([headerLine + text], { type: 'text/markdown;charset=utf-8' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `overview-${collapsibleLabel.toLowerCase().replace(/[^a-z0-9]+/g, '-')}.md`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                  className="ms-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-[color:var(--vie-accent)] hover:bg-[color:var(--vie-accent-muted)] transition-colors"
                  aria-label={`Export ${starredHighlights.size} starred items`}
                >
                  <Download className="h-3 w-3" aria-hidden="true" />
                  Export {starredHighlights.size}
                </button>
              )}
            </div>
          }
        >
          <ul className="space-y-2">
            {/* The <li> stays the list's direct child (FadeIn renders a <div>). */}
            {collapsibleItems.map((item, i) => (
              <li key={i}>
                <FadeIn index={i} className="flex items-start gap-2.5 text-sm leading-relaxed group">
                  <span className="text-lg shrink-0" aria-hidden="true">{item.emoji}</span>
                  <span className={cn(
                    'text-muted-foreground flex-1',
                    starredHighlights.has(i) && 'font-semibold text-foreground',
                  )}>
                    {item.text}
                  </span>
                  {videoId && (
                    <button
                      onClick={() => toggleStar(i)}
                      className="shrink-0 p-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
                      aria-label={starredHighlights.has(i) ? 'Remove bookmark' : 'Bookmark'}
                    >
                      <Star
                        className={cn(
                          'h-3.5 w-3.5 transition-colors',
                          starredHighlights.has(i)
                            ? 'fill-warning text-warning'
                            : 'text-muted-foreground hover:text-warning',
                        )}
                      />
                    </button>
                  )}
                </FadeIn>
              </li>
            ))}
          </ul>
        </ExpandableCard>
      )}

      {tips && tips.length > 0 && (
        <ExpandableCard
          defaultExpanded
          header={
            <div className="flex items-center gap-2">
              <span className="type-eyebrow text-info">{t.tips}</span>
              <Badge variant="info" className="tabular-nums">{tips.length}</Badge>
            </div>
          }
        >
          <ul className="space-y-1.5">
            {tips.map((tip, i) => (
              <li key={i} className="flex items-baseline gap-2 text-sm leading-relaxed text-muted-foreground">
                <span className="w-1 h-1 rounded-full bg-info/70 shrink-0 translate-y-1.5" />
                {tip}
              </li>
            ))}
          </ul>
        </ExpandableCard>
      )}
    </div>
  );
});
