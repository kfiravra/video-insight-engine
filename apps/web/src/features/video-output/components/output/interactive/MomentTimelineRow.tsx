import { memo, useState } from 'react';
import { Check, Clock, Maximize2, Play, Share2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Badge, EvidenceImage, FadeIn, GlassCard } from '@/components/vie';
import { cn } from '@/lib/utils';
import { useLabels } from '@/lib/i18n';

import { getMoodColor } from '../../../lib/moodColors';
import { QuoteLine } from './QuoteLine';
import { formatDuration, isClip, resolveLabel, type MomentItem } from './moment-utils';

interface MomentTimelineRowProps {
  item: MomentItem;
  originalIndex: number;
  /** The track is an interview's Q&A (any row carries a `question`): every row
   *  leads its answer with "A · speaker". Decided per track because rows stored
   *  before 4.1 dropped `question` when it equalled the label. */
  isExchange: boolean;
  /** Position within the filtered list — drives the FadeIn stagger. */
  index: number;
  isActive: boolean;
  pulsing: boolean;
  copied: boolean;
  totalDuration: number;
  currentTime?: number;
  onSeek?: (seconds: number) => void;
  /** Open this moment's image full-size (lightbox). */
  onExpand?: (originalIndex: number) => void;
  onShare: (item: MomentItem, index: number) => void;
}

/**
 * One timeline row: spine marker + card with the moment text LEADING and the
 * media column TRAILING (end side). The media column exists only when the
 * moment has a frame — a frameless row is text alone, no empty plate. The
 * frame sits at a 10rem base width and grows to full width only when the
 * user clicks it (click again to shrink) — never on hover, scroll position or
 * playback, so nothing pops open while reading. Grown media wraps to its own
 * line beneath the text, so the row top never moves. The image always fills
 * its fixed 16:9 frame (object-fit cover). The corner button shows it
 * full-size (lightbox); seeking is only via the timestamp chip and the
 * explicit Jump/Play action. LLM frame metadata (caption/OCR/scene type)
 * never renders — internal signal only.
 */
export const MomentTimelineRow = memo(function MomentTimelineRow({
  item,
  originalIndex,
  isExchange,
  index,
  isActive,
  pulsing,
  copied,
  totalDuration,
  currentTime,
  onSeek,
  onExpand,
  onShare,
}: MomentTimelineRowProps) {
  // Frame preview size — the user's choice alone, per row.
  const [expanded, setExpanded] = useState(false);

  const t = useLabels();
  const clip = isClip(item);
  const moodColor = getMoodColor(item.mood);
  const duration = clip ? (item.endSeconds as number) - item.seconds : 0;
  const capsuleHeight = clip
    ? Math.max(32, Math.min(120, Math.round((duration / totalDuration) * 240)))
    : 0;
  const liveProgress =
    clip && isActive && currentTime != null
      ? Math.max(0, Math.min(duration, currentTime - item.seconds))
      : null;
  const label = resolveLabel(item);
  // A long interview question's label is only its head: the row shows it whole.
  const question = item.question?.trim();
  const questionEn = item.questionEn?.trim();
  const heading = (question && questionEn) || question || label;
  // The host's own words beneath an English heading — never a question the transcript
  // does not say (`translated`: the model's rendering is prose, D12).
  const verbatimBeneath =
    Boolean(question && questionEn && question !== questionEn) && item.questionStatus !== 'translated';
  // An exchange row: the speaker answers, so the chip must not sit in the header
  // where it reads as the asker — it leads the answer instead.
  const speaker = item.speaker?.trim();
  const chipContent = (
    <>
      <Clock className="h-3 w-3" aria-hidden="true" />
      {item.time}
      {clip && liveProgress != null && (
        <span className="text-muted-foreground/80"> / {formatDuration(Math.round(liveProgress))}</span>
      )}
    </>
  );

  return (
    <li
      data-kind={clip ? 'clip' : 'moment'}
      data-active={isActive ? 'true' : undefined}
      // contain-intrinsic-size gives skipped off-screen rows a height estimate
      // so the scrollbar doesn't jump as rows materialize while scrolling.
      className="group/row relative [contain:layout_paint_style] [content-visibility:auto] [contain-intrinsic-size:auto_180px]"
    >
      <FadeIn index={index}>
        <div className="flex items-stretch gap-3 ps-10">
          {/* Spine marker */}
          <div
            aria-hidden="true"
            className={cn(
              'absolute start-[8px] flex flex-col items-center',
              clip ? 'top-3 bottom-3' : 'top-3',
            )}
          >
            {clip ? (
              <div
                data-testid={`capsule-${originalIndex}`}
                style={{ height: `${capsuleHeight}px` }}
                className={cn(
                  'relative w-[14px] rounded-full border border-[var(--vie-accent)]/40 bg-[var(--vie-accent)]/15',
                  'shadow-[inset_0_0_12px_oklch(from_var(--vie-accent)_l_c_h/0.25)]',
                  isActive && 'ring-1 ring-[var(--vie-accent)]/60 bg-[var(--vie-accent)]/30',
                )}
              >
                <span className="absolute -top-1 start-1/2 -translate-x-1/2 size-2 rounded-full bg-[var(--vie-accent)]" aria-hidden="true" />
                <span className="absolute -bottom-1 start-1/2 -translate-x-1/2 size-2 rounded-full bg-[var(--vie-accent)]/70" aria-hidden="true" />
              </div>
            ) : (
              <div
                className={cn(
                  'size-[14px] rounded-full border-2 bg-background',
                  isActive
                    ? 'border-[var(--vie-accent)] ring-2 ring-[var(--vie-accent)]/30'
                    : 'border-[var(--vie-accent)]/45',
                  pulsing && 'animate-pulse-once',
                )}
              />
            )}
          </div>

          {/* Card body — everything visible at rest. Clicks are for actions
              only (seek, share), never for revealing content. */}
          <GlassCard
            variant={isActive ? 'default' : 'outlined'}
            className={cn(
              'flex-1 overflow-hidden p-0',
              isActive && 'ring-1 ring-[var(--vie-accent)]/40',
            )}
          >
            <div className="px-3 py-2.5 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                {onSeek ? (
                  <button
                    type="button"
                    onClick={() => onSeek(item.seconds)}
                    dir="ltr"
                    className="inline-flex items-center gap-1 text-xs font-bold tabular-nums text-[var(--vie-accent)] bg-[var(--vie-accent)]/10 px-2 py-0.5 rounded-md shrink-0 cursor-pointer hover:bg-[var(--vie-accent)]/20 transition-colors"
                    aria-label={`Jump to ${item.time}`}
                  >
                    {chipContent}
                  </button>
                ) : (
                  <span dir="ltr" className="inline-flex items-center gap-1 text-xs font-bold tabular-nums text-[var(--vie-accent)] bg-[var(--vie-accent)]/10 px-2 py-0.5 rounded-md shrink-0">
                    {chipContent}
                  </span>
                )}
                {clip && (
                  <span dir="ltr" className="text-[11px] font-medium tabular-nums text-muted-foreground">
                    {formatDuration(duration)}
                  </span>
                )}
                {item.emoji && <span aria-hidden="true" className="text-base">{item.emoji}</span>}
                {item.mood && (
                  <span
                    className="inline-flex items-center gap-1.5"
                    aria-label={`Mood: ${item.mood}`}
                  >
                    <span
                      aria-hidden="true"
                      className="inline-block size-1.5 rounded-full"
                      style={{ background: moodColor }}
                    />
                    <Badge variant="muted" className="text-xs font-medium capitalize">
                      {item.mood}
                    </Badge>
                  </span>
                )}
                {speaker && !isExchange && (
                  <span className="text-xs font-medium text-muted-foreground/70 italic">
                    {speaker}
                  </span>
                )}
              </div>

              {/* Text leads, media trails (end side). The media column exists
                  only for framed moments, at 10rem base width; a click on the
                  frame grows it to full width (and back) — the grown figure
                  wraps onto its own line BELOW the text, so the row top stays
                  put. */}
              <div className="flex flex-wrap items-start gap-3">
                <div className="flex-1 basis-56 min-w-0 space-y-1.5">
                  {/* No clamp: in this view the label IS the substance for
                      most videos (R7), so it always renders in full. */}
                  <p className="text-sm font-semibold leading-snug">{heading}</p>
                  {verbatimBeneath && <QuoteLine quote={question} size="xs" />}
                  {isExchange && (item.description || speaker) ? (
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {speaker && (
                        <span className="me-1 font-semibold text-foreground/80">
                          {t.answerBy.replace('{speaker}', speaker)}
                        </span>
                      )}
                      {item.description}
                    </p>
                  ) : item.description && item.description !== label && (
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {item.description}
                    </p>
                  )}
                  {/* Verbatim beats paraphrase (R10): the speaker's own line, one quiet line;
                      QuoteLine adds the English beneath a non-English line (D12). */}
                  <QuoteLine quote={item.quote} quoteEn={item.quoteEn} translated={item.quoteTranslated} size="xs" />
                  {item.tags && item.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {item.tags.map((tag, i) => (
                        <Badge key={i} variant="muted" className="text-xs">{tag}</Badge>
                      ))}
                    </div>
                  )}
                </div>

                {item.thumbnailUrl && (
                  <figure
                    data-slot="moment-media"
                    data-expanded={expanded ? 'true' : undefined}
                    className={cn(
                      'shrink-0 transition-[width] duration-[420ms] ease-[var(--ease-out-quint)] motion-reduce:transition-none',
                      // Exclusive widths: a responsive `md:w-48` would outrank a bare `w-full`.
                      expanded ? 'w-full' : 'w-40 md:w-48',
                    )}
                  >
                    <EvidenceImage
                      src={item.thumbnailUrl}
                      alt={item.frameCaption ?? `Frame at ${item.time}`}
                      className="rounded-md border-border/30"
                      // Pinned to the 16:9 box so any thumbnail ratio fills it edge to edge.
                      imgClassName="absolute inset-0"
                    >
                      {/* The whole frame toggles its size; the corner button opens the lightbox. */}
                      <button
                        type="button"
                        onClick={() => setExpanded((v) => !v)}
                        aria-expanded={expanded}
                        aria-label={`${expanded ? 'Shrink' : 'Enlarge'} frame: ${label}`}
                        data-testid={`timeline-toggle-${originalIndex}`}
                        className={cn(
                          'absolute inset-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--vie-accent)]/60',
                          expanded ? 'cursor-zoom-out' : 'cursor-zoom-in',
                        )}
                      />
                      {onExpand && (
                        <button
                          type="button"
                          onClick={() => onExpand(originalIndex)}
                          aria-label={`View image: ${label}`}
                          data-testid={`timeline-expand-${originalIndex}`}
                          className="absolute bottom-1.5 end-1.5 z-10 inline-flex items-center rounded-md bg-background/80 p-1 text-foreground/80 backdrop-blur transition-colors hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--vie-accent)]/60"
                        >
                          <Maximize2 className="h-3 w-3 shrink-0" aria-hidden="true" />
                        </button>
                      )}
                    </EvidenceImage>
                  </figure>
                )}
              </div>

              {/* Row actions — always visible, compact */}
              <div className="flex flex-wrap items-center gap-2">
                {onSeek && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onSeek(item.seconds)}
                    className="h-7 px-2 text-xs gap-1.5"
                  >
                    <Play className="h-3 w-3" aria-hidden="true" />
                    {clip ? 'Play range' : 'Jump to'}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onShare(item, originalIndex)}
                  className={cn(
                    'h-7 px-2 text-xs gap-1.5',
                    copied ? 'text-success' : 'text-muted-foreground',
                  )}
                  data-testid={`share-${originalIndex}`}
                >
                  {copied ? (
                    <>
                      <Check className="h-3 w-3" aria-hidden="true" />
                      Link copied
                    </>
                  ) : (
                    <>
                      <Share2 className="h-3 w-3" aria-hidden="true" />
                      Share {clip ? 'clip' : 'moment'}
                    </>
                  )}
                </Button>
              </div>
            </div>
          </GlassCard>
        </div>
      </FadeIn>
    </li>
  );
});
