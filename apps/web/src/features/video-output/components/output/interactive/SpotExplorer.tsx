import { memo, useState } from 'react';
import { Star, Clock, MapPin } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';
import type { SpotItem } from '@vie/types';
import { GlassCard, FadeIn, SectionNav, Badge, MapLink, SeekChip, VisualEvidence } from '@/components/vie';
import { EmptyTabState } from './EmptyTabState';


interface SpotSection {
  label: string;
  spotIndices: number[];
}

interface SpotExplorerProps {
  spots: SpotItem[];
  sections?: SpotSection[];
  nextTab?: string;
  onNavigateTab?: (id: string) => void;
  onSeek?: (seconds: number) => void;
}

/** Normalize spot data — backend may send `title`/`detail` (KeyPoint shape) instead of `name`/`description`. */
function normalizeSpot(raw: SpotItem): SpotItem {
  const r = raw as SpotItem & { title?: string; detail?: string };
  if (!r.name && r.title) {
    return { ...r, name: r.title, description: r.description || r.detail || '' };
  }
  return r;
}

/** A seekable timestamp: a finite, non-negative number of seconds. The schema
 *  bounds it too; this guards the direct (unvalidated) render path. */
function hasSeekTimestamp(spot: SpotItem): spot is SpotItem & { timestamp: number } {
  return typeof spot.timestamp === 'number' && Number.isFinite(spot.timestamp) && spot.timestamp >= 0;
}

/** Who holds each position, shown only when the tab mixes speakers (a host and a
 *  guest): on a one-guest show the same name on every card is noise. */
function attributesSpeakers(spots: SpotItem[]): boolean {
  const speakers = new Set(spots.map((s) => s.speaker?.trim()).filter(Boolean));
  return speakers.size >= 2;
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
  const showSpeaker = attributesSpeakers(spots);
  const useGrid = spots.length > 8 && hasFrames;

  const renderSpotCard = (spotIndex: number, fadeIdx: number) => {
    const spot = spots[spotIndex];
    if (!spot) return null;

    const hasFrame = Boolean(spot.thumbnailUrl || spot.frameCaption || spot.frameOcr);
    const hasActions = Boolean(spot.mapQuery || spot.bookingSearch);
    const quote = spot.quote?.trim();
    const speaker = showSpeaker ? spot.speaker?.trim() : undefined;
    const category = spot.category?.trim();

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
                {/* A key idea's kind ("Thesis", "Ask") — a quiet outline tag beside the claim. */}
                {category && (
                  <Badge variant="muted" className="border border-border/60 bg-transparent px-2 text-[11px]">
                    {category}
                  </Badge>
                )}
                {spot.pronunciation && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {spot.pronunciation}
                  </span>
                )}
                {hasSeekTimestamp(spot) && (
                  <SeekChip seconds={spot.timestamp} onSeek={onSeek} className="ms-auto shrink-0" />
                )}
              </span>
              {spot.description && (
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {spot.description}
                </p>
              )}
              {/* Verbatim beats paraphrase (R10): the speaker's own line, set apart. */}
              {quote && (
                <blockquote className="border-s-2 border-[var(--vie-accent)]/40 ps-2.5 text-sm italic leading-relaxed text-foreground/85">
                  {quote}
                </blockquote>
              )}
              {speaker && <p className="text-xs font-medium text-muted-foreground">— {speaker}</p>}
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
