import { memo, useCallback, useState } from 'react';
import { useLabels } from '@/lib/i18n';
import { useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';
import { QuoteLine } from '@/features/video-output/components/output/interactive/QuoteLine';
import { Badge } from '@/components/vie/data/Badge';
import { ScoreRing } from '@/components/vie/data/ScoreRing';
import { SeekChip } from '@/components/vie/data/SeekChip';
import { CollapsibleSection } from './CollapsibleSection';
import { HeroWorthIt } from './HeroWorthIt';
import type { HeroSubScore, HeroVerdictData } from '@/features/video-output/lib/hero-verdict';

const BADGE_TONE: Record<string, 'success' | 'warning' | 'destructive'> = {
  recommended: 'success',
  best_in_class: 'success',
  conditional: 'warning',
  not_recommended: 'destructive',
};

interface HeroVerdictProps {
  verdict: HeroVerdictData;
  /** The hero call's lines: each stands in where the reviewer names no Best for / Not for. */
  worthItFor?: string;
  skipIf?: string;
  /** Phones start collapsed, so the verdict row costs no more than the "Worth it?" row (4.4). */
  isPhone: boolean;
}

/** The reviewer's call as a tone pill beside the row label, so a collapsed row
 *  still says it — in the page's language; a value with no label prints as stored. */
function VerdictBadge({ badge }: { badge: string }) {
  const t = useLabels();
  return (
    <Badge
      variant={BADGE_TONE[badge] ?? 'muted'}
      className="py-0 text-[0.6875rem] leading-[0.875rem] font-semibold uppercase tracking-wider"
    >
      {t.verdictBadges[badge] ?? badge.replace(/_/g, ' ')}
    </Badge>
  );
}

interface VerdictStatementProps {
  verdict: HeroVerdictData;
  onSeek: (seconds: number) => void;
}

/** The bottom line with a ▶ chip to where it is said; a score ring only when
 *  assembly ships the reviewer's stated score (D13 — never derived). */
function VerdictStatement({ verdict, onSeek }: VerdictStatementProps) {
  const t = useLabels();
  const { bottomLine, timestamp, score, maxScore = 10 } = verdict;
  return (
    <div className="flex items-start gap-4">
      {score !== undefined && <ScoreRing score={score} total={maxScore} label={t.score} size="sm" />}
      <p className="min-w-0 text-sm font-medium leading-relaxed text-foreground/90 text-pretty">
        {bottomLine}
        {timestamp !== undefined && <SeekChip seconds={timestamp} onSeek={onSeek} className="ms-1.5 align-baseline" />}
      </p>
    </div>
  );
}

interface SubScoreListProps {
  subScores: HeroSubScore[];
  maxScore?: number;
}

/** The categories the reviewer scores on camera, as assembly kept them (stated
 *  rows only, D13): one wrapping line of "category score/max" chips, the scale
 *  only when stated. */
function SubScoreList({ subScores, maxScore }: SubScoreListProps) {
  const t = useLabels();
  return (
    <ul aria-label={t.subScores} className="flex flex-wrap gap-1.5">
      {subScores.map((sub, i) => (
        <li
          key={`${sub.category}-${i}`}
          className="inline-flex items-baseline gap-1.5 rounded-md border border-border/50 bg-muted/30 px-2 py-0.5 text-xs"
        >
          <span className="text-muted-foreground">{sub.category}</span>
          <span dir="ltr" className="font-semibold tabular-nums text-foreground/90">
            {maxScore ? `${sub.score}/${maxScore}` : sub.score}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** A review's verdict in the hero (output-quality 4.3, Kfir 2026-10-22: the
 *  hero only): a "Verdict" row whose badge says the call, opening onto the
 *  bottom line, its moment, the reviewer's words and stated sub-scores; Best
 *  for / Not for take the place of Worth it / Skip if. The row starts
 *  collapsed: on desktop the two lines stay open under it, one line each, so
 *  the hero keeps to the first screen (4.6a); on phones they sit inside it,
 *  like the rows above it (4.4). */
export const HeroVerdict = memo(function HeroVerdict({ verdict, worthItFor, skipIf, isPhone }: HeroVerdictProps) {
  const t = useLabels();
  const { seekTo } = useVideoPlayer();
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((prev) => !prev), []);
  const lines = (
    <HeroWorthIt
      worthItFor={worthItFor}
      skipIf={skipIf}
      bestFor={verdict.bestFor}
      notFor={verdict.notFor}
      oneLine={!isPhone}
    />
  );
  return (
    <div className="space-y-1">
      <CollapsibleSection
        baseId="vie-hero-verdict"
        label={t.verdict}
        accessory={verdict.badge ? <VerdictBadge badge={verdict.badge} /> : undefined}
        open={open}
        onToggle={toggle}
      >
        <div className="space-y-4">
          <div className="space-y-2">
            <VerdictStatement verdict={verdict} onSeek={seekTo} />
            <QuoteLine quote={verdict.quote} quoteEn={verdict.quoteEn} translated={verdict.quoteTranslated} />
            {verdict.subScores && <SubScoreList subScores={verdict.subScores} maxScore={verdict.maxScore} />}
          </div>
          {isPhone && lines}
        </div>
      </CollapsibleSection>
      {!isPhone && lines}
    </div>
  );
});
