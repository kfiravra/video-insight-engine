import { memo } from 'react';
import { motion } from 'motion/react';
import { cn } from '@/lib/utils';
import { springs } from '@/lib/motion';
import { useLabels, type Labels } from '@/lib/i18n';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';
import { YouTubePlayer } from '@/components/videos/YouTubePlayer';
import { PendingTabPill } from './PendingTabPill';
import { PlayCloseGlyph } from './PlayCloseGlyph';
import { DOMAIN_META } from './domain-meta';
import { HeroBrief } from './HeroBrief';
import { HeroDigest } from './HeroDigest';
import { VideoHeroStrip, type VideoHeroTab } from './VideoHeroStrip';
import type { ContentTag } from '@vie/types';
import type { HeroVerdictData } from '@/features/video-output/lib/hero-verdict';

interface PendingTab {
  id: string;
  label: string;
  emoji: string;
}

interface VideoHeroProps {
  title: string;
  creator?: string;
  duration?: number | null;
  tldr?: string;
  /** Each a claim closing with the `[m:ss]` (`[h:mm:ss]`) where it is said —
   *  the marker renders as a ▶ seek chip, never as text. */
  keyTakeaways?: string[];
  /** The hero rewrite's 3–5 sentence brief (output-quality 2.2). Wins over
   *  `masterSummary`, which older rows carry instead. */
  brief?: string;
  /** The long-form brief (synthesis.masterSummary). Rendered as a collapsible
   *  "Brief" under the tldr — open on desktop, collapsed on phones (R11). */
  masterSummary?: string;
  /** "Worth it for / Skip if" — two short lines under the takeaways (C2). */
  worthItFor?: string;
  skipIf?: string;
  /** A review's verdict, lifted out of its comparison tab (output-quality 4.3):
   *  a "Verdict" row in the worth-it lines' place. */
  verdict?: HeroVerdictData;
  /** Minutes to read the page: the identity strip then shows
   *  "Read: N min · Watch: M min" in place of the bare duration. */
  readingMinutes?: number;
  /** The question the video answers (extraction keyQuestion). First line of
   *  the brief when present. */
  keyQuestion?: string;
  youtubeId?: string;
  tabs?: VideoHeroTab[];
  activeTabId?: string;
  onTabSelect?: (id: string) => void;
  completedTabs?: Set<string>;
  domainGradient?: string;
  /** Primary content tag — drives the identity-strip domain emoji + label and
   *  the fallback emoji selection on takeaways that don't match a keyword. */
  primaryTag?: ContentTag;
  /** Optional difficulty / depth label shown in the identity strip ("Beginner",
   *  "Advanced", etc). */
  level?: string;
  /** Explicit streaming flag. When omitted, falls back to inferring from a
   *  missing tldr (legacy behavior) — pass it wherever the caller knows the
   *  real stream state so a completed video with an empty brief doesn't
   *  render skeletons forever. */
  isStreaming?: boolean;
  /** Planned tabs announced by the stream that haven't landed yet. Rendered
   *  as inert pills while streaming: before any tab exists, in the exact
   *  geometry of the final strip; once tabs exist, after the live ones. */
  pendingTabs?: PendingTab[];
  className?: string;
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

const PHONE_QUERY = '(max-width: 767px)';
// Stable empty list so memo(VideoHeroStrip) bails out when nothing is pending.
const NO_PENDING: PendingTab[] = [];
const NO_TABS: VideoHeroTab[] = [];
const NO_TAKEAWAYS: string[] = [];

interface ReadWatch {
  read: string;
  watch?: string;
}

/** "Read: 5 min" + "Watch: 63 min" — the page's compression promise. Null
 *  when the page carries no reading time (older rows keep the bare duration). */
function readWatchLabels(t: Labels, readingMinutes?: number, duration?: number | null): ReadWatch | null {
  if (readingMinutes == null || !Number.isFinite(readingMinutes) || readingMinutes <= 0) return null;
  const read = t.readMinutes.replace('{minutes}', String(Math.max(1, Math.round(readingMinutes))));
  if (!duration || duration <= 0) return { read };
  return { read, watch: t.watchMinutes.replace('{minutes}', String(Math.max(1, Math.round(duration / 60)))) };
}

interface IdentityStripProps {
  primaryTag?: ContentTag;
  level?: string;
  durationStr: string;
  readWatch: ReadWatch | null;
  creator?: string;
}

/** Identity strip — domain, level, duration (or read / watch time) and
 *  creator as a single row, separated by dots so the metadata reads as one
 *  cohesive line. */
function IdentityStrip({ primaryTag, level, durationStr, readWatch, creator }: IdentityStripProps) {
  const domainMeta = primaryTag ? DOMAIN_META[primaryTag] : undefined;
  const chips: Array<{ key: string; node: React.ReactNode }> = [];
  if (domainMeta) {
    chips.push({
      key: 'domain',
      node: (
        <span className="inline-flex items-center gap-1.5 font-medium text-foreground/85">
          <span aria-hidden="true" className="text-sm leading-none">{domainMeta.emoji}</span>
          <span>{domainMeta.label}</span>
        </span>
      ),
    });
  }
  if (level) chips.push({ key: 'level', node: <span>{level}</span> });
  if (readWatch) {
    chips.push({ key: 'read', node: <span className="font-medium text-foreground/85 tabular-nums">{readWatch.read}</span> });
    if (readWatch.watch) chips.push({ key: 'watch', node: <span className="tabular-nums">{readWatch.watch}</span> });
  } else if (durationStr) {
    chips.push({ key: 'duration', node: <span className="tabular-nums">{durationStr}</span> });
  }
  if (creator) chips.push({ key: 'creator', node: <span className="truncate max-w-[24ch]">{creator}</span> });
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
      {chips.map((chip, i) => (
        <span key={chip.key} className="inline-flex items-center gap-2.5">
          {i > 0 && <span aria-hidden="true" className="text-muted-foreground/40">·</span>}
          {chip.node}
        </span>
      ))}
    </div>
  );
}

/** Whether the identity strip has anything to show (the title then sits below it). */
function hasIdentity({ primaryTag, level, durationStr, readWatch, creator }: IdentityStripProps): boolean {
  return Boolean(primaryTag || level || durationStr || readWatch || creator);
}

/** Streaming strip: the tab plan (or skeleton pills before it's known)
 *  rendered in the exact geometry of the live strip, so real tabs arriving
 *  reads as a fill-in rather than a layout swap. Inert and hidden from AT —
 *  the StreamingPlaceholder timeline carries progress semantics. */
function PendingStrip({ pendingTabs }: { pendingTabs: PendingTab[] }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'flex items-center gap-1.5 overflow-x-hidden',
        'border-t border-border/60 rounded-b-2xl px-3 py-2.5 md:px-4',
        '[mask-image:linear-gradient(to_right,transparent,black_16px,black_calc(100%-16px),transparent)]',
      )}
    >
      {pendingTabs.length > 0
        ? pendingTabs.map((tab, i) => <PendingTabPill key={tab.id} label={tab.label} emoji={tab.emoji} index={i} />)
        : [88, 104, 72, 96].map((width, i) => (
            <div key={i} className="h-9 shrink-0 rounded-xl bg-muted/25 skeleton-breathe" style={{ width }} />
          ))}
    </div>
  );
}

interface HeroHeaderProps {
  title: string;
  identity: IdentityStripProps;
  youtubeId?: string;
}

/** Top row: identity strip + title stack on the left; the watch control pins
 *  to the right corner as bare line-art whose edges morph into a close mark
 *  when the player opens (PlayCloseGlyph). The oversized invisible button
 *  keeps a comfortable touch target around the glyph. */
function HeroHeader({ title, identity, youtubeId }: HeroHeaderProps) {
  const t = useLabels();
  const { togglePlayer, isPlayerOpen } = useVideoPlayer();
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <IdentityStrip {...identity} />
        <h2
          id="vie-hero-title"
          className={cn(
            'font-semibold tracking-tight leading-[1.15] text-balance break-words line-clamp-2 text-xl md:text-2xl',
            hasIdentity(identity) && 'mt-2.5',
          )}
        >
          {title || t.processing}
        </h2>
      </div>
      {youtubeId && (
        <button
          type="button"
          onClick={togglePlayer}
          className="play-control -mr-1 -mt-1 cursor-pointer flex size-12 shrink-0 items-center justify-center md:size-14"
          aria-label={isPlayerOpen ? t.hide : t.watch}
          aria-expanded={isPlayerOpen}
        >
          <PlayCloseGlyph open={isPlayerOpen} className="size-8 shrink-0 md:size-9" />
        </button>
      )}
    </div>
  );
}

/** The inline player, morphing open under the header. */
function HeroPlayer({ youtubeId }: { youtubeId: string }) {
  const { isPlayerOpen, playerRef, registerPlayerAnchor } = useVideoPlayer();
  return (
    <div
      // Registered as the seek scroll anchor: a timestamp click far down
      // the page scrolls this wrapper back into view before the seek runs.
      ref={registerPlayerAnchor}
      className={cn(
        'grid transition-[grid-template-rows,opacity] duration-300 ease-[var(--ease-out-expo)]',
        isPlayerOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
      )}
    >
      <div className="overflow-hidden">
        <div className="px-6 pb-4 md:px-7 pt-4">
          <YouTubePlayer ref={playerRef} youtubeId={youtubeId} className="rounded-lg" />
        </div>
      </div>
    </div>
  );
}

/** The tldr under the title, or its skeleton while the stream is still writing it. */
function Tldr({ tldr, isStreaming }: { tldr?: string; isStreaming: boolean }) {
  if (tldr) {
    return <p className="mt-3 max-w-[68ch] text-[0.95rem] leading-relaxed text-foreground/85 text-pretty">{tldr}</p>;
  }
  if (!isStreaming) return null;
  return (
    <div className="mt-3 space-y-1.5" aria-hidden="true">
      <div className="h-3.5 w-full rounded bg-muted/30 animate-pulse" />
      <div className="h-3.5 w-4/5 rounded bg-muted/30 animate-pulse" />
      <div className="h-3.5 w-3/5 rounded bg-muted/30 animate-pulse" />
    </div>
  );
}

interface HeroTopProps {
  hero: VideoHeroProps;
  isStreaming: boolean;
  isPhone: boolean;
  /** Nothing renders below the header: it carries its own bottom padding. */
  padBottom: boolean;
}

/** The header block: identity strip + title + watch control, the tldr, the brief. */
function HeroTop({ hero, isStreaming, isPhone, padBottom }: HeroTopProps) {
  const t = useLabels();
  const { title, creator, duration, tldr, brief, masterSummary, keyQuestion, readingMinutes } = hero;
  const { primaryTag, level, youtubeId } = hero;
  const readWatch = readWatchLabels(t, readingMinutes, duration);
  const identity = { primaryTag, level, durationStr: formatDuration(duration), readWatch, creator };
  // Older rows carry only `masterSummary`; the hero rewrite's brief wins.
  const briefText = brief?.trim() ? brief : masterSummary;
  return (
    <div className={cn('px-6 pt-5 md:px-7 md:pt-6', padBottom && 'pb-5 md:pb-6')}>
      <HeroHeader title={title} identity={identity} youtubeId={youtubeId} />
      <Tldr tldr={tldr} isStreaming={isStreaming} />
      {briefText?.trim() && <HeroBrief masterSummary={briefText} keyQuestion={keyQuestion} isPhone={isPhone} />}
    </div>
  );
}

interface HeroFlagsInput {
  tldr?: string;
  keyTakeaways: string[];
  worthItFor?: string;
  skipIf?: string;
  hasVerdict: boolean;
  hasTabs: boolean;
  /** The caller's explicit streaming flag, when it passed one. */
  isStreamingProp?: boolean;
  isPlayerOpen: boolean;
}

interface HeroFlags {
  isStreaming: boolean;
  showPendingStrip: boolean;
  /** Once tabs exist, the planned ones still to come trail the live strip. */
  showTrailingPending: boolean;
  showTakeaways: boolean;
  showWorth: boolean;
  headerNeedsBottomPad: boolean;
}

/** What the hero shows, derived from its props and the player state. */
function heroFlags({
  tldr,
  keyTakeaways,
  worthItFor,
  skipIf,
  hasVerdict,
  hasTabs,
  isStreamingProp,
  isPlayerOpen,
}: HeroFlagsInput): HeroFlags {
  const isStreaming = isStreamingProp ?? !tldr;
  // The pending pills render only while streaming, in the same geometry as the
  // live strip so the arrival is a fill-in, not a jump. Requires the explicit
  // prop: legacy callers that omit it get the skeleton treatment from
  // `isStreaming`'s tldr heuristic but never the strip.
  const pendingKnown = isStreaming && isStreamingProp !== undefined;
  const showPendingStrip = !hasTabs && pendingKnown;
  const showTakeaways = keyTakeaways.length > 0 || isStreaming;
  const showWorth = hasVerdict || Boolean(worthItFor?.trim() || skipIf?.trim());
  return {
    isStreaming,
    showPendingStrip,
    showTrailingPending: hasTabs && pendingKnown,
    showTakeaways,
    showWorth,
    // When nothing renders below the header (no takeaways, no worth-it lines, no
    // tab strip, player closed), the header must carry its own bottom padding so
    // the card doesn't look clipped. Every other block supplies its own trailing space.
    headerNeedsBottomPad: !showTakeaways && !showWorth && !hasTabs && !showPendingStrip && !isPlayerOpen,
  };
}

export const VideoHero = memo(function VideoHero(props: VideoHeroProps) {
  const { tldr, keyTakeaways = NO_TAKEAWAYS, worthItFor, skipIf, verdict, youtubeId, className } = props;
  const { tabs = NO_TABS, activeTabId, onTabSelect, completedTabs, domainGradient } = props;
  const { isStreaming: isStreamingProp, pendingTabs = NO_PENDING } = props;
  const isPhone = useMediaQuery(PHONE_QUERY);
  const { isPlayerOpen } = useVideoPlayer();
  const hasTabs = tabs.length > 0;
  const hasVerdict = verdict !== undefined;
  const flags = heroFlags({ tldr, keyTakeaways, worthItFor, skipIf, hasVerdict, hasTabs, isStreamingProp, isPlayerOpen });

  return (
    <motion.section
      className={cn('relative w-full rounded-2xl border border-border bg-card', className)}
      // While streaming, the hero is the landing element of the
      // "stream-input-spine" view transition — the URL input morphs into
      // this card on navigate. Scoped to streaming so the name can't
      // collide with a sidebar intake form on an already-finished page.
      style={flags.isStreaming ? ({ viewTransitionName: 'vie-input-spine' } as React.CSSProperties) : undefined}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...springs.soft, delay: 0.04 }}
      aria-labelledby="vie-hero-title"
    >
      <HeroTop hero={props} isStreaming={flags.isStreaming} isPhone={isPhone} padBottom={flags.headerNeedsBottomPad} />
      {youtubeId && <HeroPlayer youtubeId={youtubeId} />}
      <HeroDigest
        keyTakeaways={keyTakeaways}
        worthItFor={worthItFor}
        skipIf={skipIf}
        verdict={verdict}
        showTakeaways={flags.showTakeaways}
        showWorth={flags.showWorth}
        isStreaming={flags.isStreaming}
        isPhone={isPhone}
      />
      {flags.showPendingStrip && <PendingStrip pendingTabs={pendingTabs} />}
      {hasTabs && (
        <VideoHeroStrip
          tabs={tabs}
          activeTabId={activeTabId}
          onTabSelect={onTabSelect}
          completedTabs={completedTabs}
          domainGradient={domainGradient}
          trailingPending={flags.showTrailingPending ? pendingTabs : NO_PENDING}
        />
      )}
    </motion.section>
  );
});
