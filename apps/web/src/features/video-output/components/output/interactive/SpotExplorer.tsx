import { memo, useState } from 'react';
import { Star, Clock, MapPin, Play } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';
import { formatDuration } from '@/lib/string-utils';
import type { SpotItem } from '@vie/types';
import { GlassCard, FadeIn, SectionNav, Badge, MapLink, VisualEvidence } from '@/components/vie';
import { EmptyTabState } from './EmptyTabState';


interface SpotSection {
  label: string;
  spotIndices: number[];
}

/** Spot card item — `timestamp` (seconds) and `quote` arrive from the
 *  assembler ahead of the shared type (output-quality R2/R10, C4). */
interface SpotCardItem extends SpotItem {
  timestamp?: number | null;
  quote?: string | null;
}

interface SpotExplorerProps {
  spots: SpotCardItem[];
  sections?: SpotSection[];
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
  onSeek?: (seconds: number) => void;
}

/** Normalize spot data — backend may send `title`/`detail` (KeyPoint shape) instead of `name`/`description`. */
function normalizeSpot(raw: SpotCardItem): SpotCardItem {
  const r = raw as SpotCardItem & { title?: string; detail?: string };
  if (!r.name && r.title) {
    return { ...r, name: r.title, description: r.description || r.detail || '' };
  }
  return r;
}

function hasSeekTimestamp(spot: SpotCardItem): spot is SpotCardItem & { timestamp: number } {
  return typeof spot.timestamp === 'number' && Number.isFinite(spot.timestamp);
}

interface SpotSeekChipProps {
  seconds: number;
  onSeek?: (seconds: number) => void;
}

/** Timestamp chip — same accent chip as the moment timeline; a button only
 *  when a seek handler exists, otherwise a static marker. */
function SpotSeekChip({ seconds, onSeek }: SpotSeekChipProps) {
  const time = formatDuration(seconds);
  const chipClass =
    'ms-auto inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-bold tabular-nums shrink-0 text-[var(--vie-accent)] bg-[var(--vie-accent)]/10';
  if (!onSeek) {
    return (
      <span dir="ltr" className={chipClass}>
        {time}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onSeek(seconds)}
      dir="ltr"
      aria-label={`Jump to ${time}`}
      className={cn(chipClass, 'cursor-pointer transition-colors hover:bg-[var(--vie-accent)]/20')}
    >
      <Play className="h-3 w-3 fill-current" aria-hidden="true" />
      {time}
    </button>
  );
}

/** SectionNav id for the synthetic "All" pill — no section label can collide. */
const ALL_SECTION = '__all__';

export const SpotExplorer = memo(function SpotExplorer({
  spots: rawSpots,
  sections,
  onSeek,
}: SpotExplorerProps) {
  const spots = rawSpots.map(normalizeSpot);
  const [activeSection, setActiveSection] = useState(ALL_SECTION);
  const t = useLabels();

  if (spots.length === 0)
    return <EmptyTabState message="No highlights were extracted for this video." icon={MapPin} />;

  const sectionNav = sections && sections.length > 1
    ? [
        { id: ALL_SECTION, label: `${t.all} (${spots.length})` },
        ...sections.map((s) => ({ id: s.label, label: `${s.label} (${s.spotIndices.length})` })),
      ]
    : null;

  const activeSectionObj = sections?.find((s) => s.label === activeSection);
  const visibleIndices = activeSectionObj?.spotIndices ?? spots.map((_, i) => i);

  // Dense frame-led collections read better as a gallery grid than a single
  // tall column — only when there are enough spots AND frames to justify it.
  const hasFrames = spots.some((s) => Boolean(s.thumbnailUrl || s.frameCaption || s.frameOcr));
  const useGrid = spots.length > 8 && hasFrames;

  const renderSpotCard = (spotIndex: number, fadeIdx: number) => {
    const spot = spots[spotIndex];
    if (!spot) return null;

    const hasFrame = Boolean(spot.thumbnailUrl || spot.frameCaption || spot.frameOcr);
    const hasActions = Boolean(spot.mapQuery || spot.bookingSearch);

    return (
      <FadeIn key={spotIndex} index={fadeIdx}>
        <div className="rounded-lg border border-border/50 overflow-hidden">
          {/* Frame-as-Hero: the supporting frame leads the card at aspect-video,
              never a sidekick thumbnail (DESIGN.md §6). */}
          {hasFrame && (
            <VisualEvidence
              variant="figure"
              thumbnailUrl={spot.thumbnailUrl}
              caption={spot.frameCaption}
              ocr={spot.frameOcr}
              sceneType={spot.frameSceneType}
              evidence={spot.frameEvidence}
              className="p-3 pb-0"
            />
          )}
          {/* Everything is visible at rest — no accordion, no "Show more".
              Clicking is reserved for actions (map, booking). */}
          <div className="px-4 py-3 flex items-start gap-3">
            <span className="text-base shrink-0 mt-0.5" aria-hidden="true">{spot.emoji}</span>
            <div className="flex-1 min-w-0 space-y-1.5">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="font-semibold text-[0.9375rem]">{spot.name}</span>
                {spot.pronunciation && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {spot.pronunciation}
                  </span>
                )}
                {hasSeekTimestamp(spot) && <SpotSeekChip seconds={spot.timestamp} onSeek={onSeek} />}
              </span>
              {spot.description && (
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {spot.description}
                </p>
              )}
              {/* Verbatim beats paraphrase (R10): the speaker's own line, set apart. */}
              {spot.quote?.trim() && (
                <blockquote className="border-s-2 border-[var(--vie-accent)]/40 ps-2.5 text-sm italic leading-relaxed text-foreground/85">
                  {spot.quote.trim()}
                </blockquote>
              )}
              {spot.specs && (
                <p className="text-xs italic text-muted-foreground">
                  {spot.specs}
                </p>
              )}
              {spot.tips && (
                <p className="text-xs leading-relaxed text-muted-foreground bg-muted/20 rounded-md px-2.5 py-2">
                  <span className="me-1.5 font-bold uppercase tracking-[0.08em] text-[10px] text-[color:var(--vie-accent-ink)]">
                    {t.tip}
                  </span>
                  {spot.tips}
                </p>
              )}
              {/* Metadata badges */}
              <div className="flex flex-wrap items-center gap-2">
                {spot.cost && (
                  <Badge variant="info" className="text-xs">{spot.cost}</Badge>
                )}
                {spot.duration && (
                  <span className="text-xs text-muted-foreground flex items-center gap-0.5">
                    <Clock className="h-2.5 w-2.5" aria-hidden="true" />
                    {spot.duration}
                  </span>
                )}
                {spot.rating != null && spot.rating > 0 && (
                  <span className="text-xs text-muted-foreground flex items-center gap-0.5">
                    <Star className="h-2.5 w-2.5 text-warning fill-warning" aria-hidden="true" />
                    {spot.rating}
                  </span>
                )}
              </div>
              {/* Actions */}
              {hasActions && (
                <div className="flex flex-wrap gap-3 pt-0.5">
                  {spot.mapQuery && <MapLink name={spot.name} query={spot.mapQuery} />}
                  {spot.bookingSearch && (
                    <a
                      href={`https://www.booking.com/searchresults.html?ss=${encodeURIComponent(spot.bookingSearch)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-[color:var(--vie-accent-ink)] hover:underline"
                    >
                      Search Booking
                    </a>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </FadeIn>
    );
  };

  return (
    <GlassCard className="space-y-4">
      {/* Section navigation */}
      {sectionNav && (
        <SectionNav
          sections={sectionNav}
          activeId={activeSection}
          onSelect={setActiveSection}
        />
      )}

      {/* Section header when sections are active */}
      {sections && sections.length > 1 && activeSectionObj && (
        <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground/70 px-1">
          {activeSectionObj.label}
        </h3>
      )}

      {/* Spot list — dense frame-led collections switch to a gallery grid */}
      <div className={cn(useGrid ? 'grid gap-3 sm:grid-cols-2 lg:grid-cols-3' : 'space-y-1.5')}>
        {visibleIndices.map((spotIndex, fadeIdx) => renderSpotCard(spotIndex, fadeIdx))}
      </div>

    </GlassCard>
  );
});
