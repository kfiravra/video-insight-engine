import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type CSSProperties,
} from 'react';
import { Clock, Music } from 'lucide-react';

import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { GlassCard, SectionNav } from '@/components/vie';
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion';
import { EmptyTabState } from './EmptyTabState';

export interface LyricsWord {
  text: string;
  startTime: number;
  endTime: number;
}

export interface LyricsLine {
  text: string;
  timestamp?: number;
  words?: LyricsWord[];
}

export interface LyricsSection {
  name: string;
  timestamp?: number;
  lines: LyricsLine[];
}

interface LyricsKaraokeProps {
  sections: LyricsSection[];
  /** Optional flat lines override — if provided, used directly and `sections` is ignored for line ordering. */
  lines?: LyricsLine[];
  artist?: string;
  onSeek?: (seconds: number) => void;
  currentTime?: number;
}

interface FlatLine extends LyricsLine {
  sectionIndex: number;
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function flattenSections(sections: LyricsSection[]): FlatLine[] {
  const out: FlatLine[] = [];
  sections.forEach((section, sIdx) => {
    section.lines.forEach((line) => {
      out.push({ ...line, sectionIndex: sIdx });
    });
  });
  return out;
}

function findActiveIndex(lines: FlatLine[], currentTime: number): number {
  // Active line is the one with the greatest timestamp <= currentTime. We scan
  // every line rather than breaking on the first later timestamp so an
  // out-of-order or zero-timestamp line can't end the search early. When no
  // line has a timestamp we return -1 and the renderer falls back to plain
  // scrolling.
  let active = -1;
  let bestTs = -Infinity;
  for (let i = 0; i < lines.length; i++) {
    const ts = lines[i].timestamp;
    if (ts == null) continue;
    if (ts <= currentTime && ts >= bestTs) {
      active = i;
      bestTs = ts;
    }
  }
  return active;
}

export const LyricsKaraoke = memo(function LyricsKaraoke({
  sections,
  lines: linesOverride,
  artist,
  onSeek,
  currentTime = 0,
}: LyricsKaraokeProps) {
  const reducedMotion = usePrefersReducedMotion();
  const flatLines = useMemo<FlatLine[]>(() => {
    if (linesOverride && linesOverride.length > 0) {
      return linesOverride.map((line) => ({ ...line, sectionIndex: 0 }));
    }
    return flattenSections(sections);
  }, [linesOverride, sections]);

  const anyTimestamps = useMemo(
    () => flatLines.some((l) => typeof l.timestamp === 'number'),
    [flatLines],
  );

  const activeIndex = useMemo(
    () => (anyTimestamps ? findActiveIndex(flatLines, currentTime) : -1),
    [flatLines, currentTime, anyTimestamps],
  );

  const containerRef = useRef<HTMLDivElement | null>(null);
  const lineRefs = useRef<Array<HTMLLIElement | null>>([]);
  const wordProgressRefs = useRef<Array<HTMLSpanElement | null>>([]);
  const rafRef = useRef<number | null>(null);
  const currentTimeRef = useRef<number>(currentTime);
  currentTimeRef.current = currentTime;
  const activeIndexRef = useRef<number>(activeIndex);
  activeIndexRef.current = activeIndex;

  // Auto-scroll the active line to the center of the lyrics container — NOT the
  // page. `scrollIntoView()` bubbles to every scrollable ancestor (including the
  // document), so mounting this list with an active line yanks the whole page to
  // center it. Scrolling `containerRef` directly keeps the jump contained to the
  // lyrics panel.
  useEffect(() => {
    if (activeIndex < 0) return;
    const container = containerRef.current;
    const node = lineRefs.current[activeIndex];
    if (!container || !node) return;
    try {
      const containerRect = container.getBoundingClientRect();
      const nodeRect = node.getBoundingClientRect();
      const delta =
        nodeRect.top - containerRect.top - (container.clientHeight - node.clientHeight) / 2;
      container.scrollTo({
        top: container.scrollTop + delta,
        behavior: reducedMotion ? 'auto' : 'smooth',
      });
    } catch {
      // jsdom/older browsers may not implement scrollTo options.
    }
  }, [activeIndex, reducedMotion]);

  // Drive the per-word karaoke fill via rAF + CSS variable. This avoids
  // re-rendering React on every frame while still hitting 60fps.
  useEffect(() => {
    const active = flatLines[activeIndexRef.current];
    if (!active?.words || active.words.length === 0) return;

    const update = (): void => {
      const t = currentTimeRef.current;
      const line = flatLines[activeIndexRef.current];
      const target = wordProgressRefs.current[activeIndexRef.current];
      if (!line?.words || !target) {
        rafRef.current = requestAnimationFrame(update);
        return;
      }
      const firstStart = line.words[0]?.startTime ?? 0;
      const lastEnd = line.words[line.words.length - 1]?.endTime ?? firstStart;
      const total = Math.max(1e-3, lastEnd - firstStart);
      const clamped = Math.min(1, Math.max(0, (t - firstStart) / total));
      target.style.setProperty(
        '--karaoke-progress',
        `${(clamped * 100).toFixed(2)}%`,
      );
      rafRef.current = requestAnimationFrame(update);
    };

    rafRef.current = requestAnimationFrame(update);
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [flatLines, activeIndex]);

  const sectionNav = useMemo(
    () =>
      sections.length > 1 && !linesOverride
        ? sections.map((s, i) => ({ id: `section-${i}`, label: s.name }))
        : null,
    [sections, linesOverride],
  );

  const activeSectionId = useMemo(() => {
    if (!sectionNav) return '';
    const line = flatLines[activeIndex];
    return line ? `section-${line.sectionIndex}` : sectionNav[0].id;
  }, [sectionNav, flatLines, activeIndex]);

  const handleSectionSelect = useCallback(
    (id: string) => {
      const idx = Number(id.replace('section-', ''));
      const section = sections[idx];
      if (section && typeof section.timestamp === 'number' && onSeek) {
        onSeek(section.timestamp);
      }
    },
    [sections, onSeek],
  );

  if (flatLines.length === 0)
    return <EmptyTabState message="No lyrics were extracted for this video." icon={Music} />;

  return (
    <div className="space-y-4" data-slot="lyrics-karaoke">
      {artist && (
        <div className="text-center">
          <span className="text-xs text-muted-foreground/70 uppercase tracking-wider">
            {artist}
          </span>
        </div>
      )}

      {sectionNav && (
        <SectionNav
          sections={sectionNav}
          activeId={activeSectionId}
          onSelect={handleSectionSelect}
        />
      )}

      <GlassCard>
        <div
          ref={containerRef}
          className="max-h-[440px] overflow-y-auto px-1 scrollbar-hide"
          data-testid="lyrics-karaoke-scroller"
        >
          <ul className="space-y-2" role="list">
            {flatLines.map((line, i) => {
              const isActive = i === activeIndex;
              const isPast = anyTimestamps && i < activeIndex;
              const isUpcoming = anyTimestamps && i > activeIndex;
              const opacity = isActive
                ? 1
                : isPast
                  ? 0.4
                  : isUpcoming
                    ? 0.7
                    : 1;
              const transformStyle: CSSProperties = {
                opacity,
                transform: isActive ? 'scale(1.1)' : 'scale(1)',
                transformOrigin: 'left center',
              };

              const seekable = Boolean(onSeek) && typeof line.timestamp === 'number';
              const handleSeek = () => {
                if (onSeek && typeof line.timestamp === 'number') {
                  onSeek(line.timestamp);
                }
              };
              return (
                <li
                  key={i}
                  ref={(el) => {
                    lineRefs.current[i] = el;
                  }}
                  data-active={isActive ? 'true' : undefined}
                  data-line-state={
                    isActive ? 'active' : isPast ? 'past' : 'upcoming'
                  }
                  role={seekable ? 'button' : undefined}
                  tabIndex={seekable ? 0 : undefined}
                  aria-label={
                    seekable ? `Jump to ${formatTime(line.timestamp as number)}` : undefined
                  }
                  className={cn(
                    'rounded-md px-3 py-1.5',
                    'transition-[transform,opacity] duration-300 ease-out motion-reduce:transition-none',
                    seekable ? 'cursor-pointer hover:bg-muted/20' : '',
                  )}
                  style={transformStyle}
                  onClick={seekable ? handleSeek : undefined}
                  onKeyDown={
                    seekable
                      ? (e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            handleSeek();
                          }
                        }
                      : undefined
                  }
                >
                  <div className="flex items-start gap-2">
                    {typeof line.timestamp === 'number' && (
                      <span
                        dir="ltr"
                        className="text-[10px] tabular-nums text-muted-foreground/50 shrink-0 mt-1"
                      >
                        {formatTime(line.timestamp)}
                      </span>
                    )}
                    <span className="relative flex-1 text-sm leading-relaxed">
                      {/* Base text always renders for accessibility + fallback. */}
                      <span dir="auto" className="block">
                        {line.text}
                      </span>
                      {/* Karaoke fill overlay: same text, masked by a CSS var
                          updated each rAF tick. Hidden when no per-word data. */}
                      {line.words && line.words.length > 0 && (
                        <span
                          ref={(el) => {
                            wordProgressRefs.current[i] = el;
                          }}
                          aria-hidden="true"
                          dir="auto"
                          data-testid={
                            isActive ? 'karaoke-active-fill' : undefined
                          }
                          className="absolute inset-0 block text-[var(--vie-accent)] pointer-events-none"
                          style={{
                            // Masked from 0% to progress; transparent after.
                            WebkitMaskImage:
                              'linear-gradient(to right, black var(--karaoke-progress, 0%), transparent var(--karaoke-progress, 0%))',
                            maskImage:
                              'linear-gradient(to right, black var(--karaoke-progress, 0%), transparent var(--karaoke-progress, 0%))',
                          }}
                        >
                          {line.text}
                        </span>
                      )}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>

        {/* If the active section has a timestamp + onSeek, surface a jump button. */}
        {sectionNav && onSeek && (
          <div className="mt-3 flex items-center justify-end">
            {(() => {
              const idx = Number(activeSectionId.replace('section-', ''));
              const ts = sections[idx]?.timestamp;
              if (typeof ts !== 'number') return null;
              return (
                <Button
                  variant="ghost"
                  size="bare"
                  onClick={() => onSeek(ts)}
                  dir="ltr"
                  className="text-xs text-[color:var(--vie-accent)] gap-1"
                >
                  <Clock className="h-3 w-3" aria-hidden="true" />
                  {formatTime(ts)}
                </Button>
              );
            })()}
          </div>
        )}
      </GlassCard>
    </div>
  );
});
