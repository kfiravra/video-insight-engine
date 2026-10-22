import { memo, useCallback, useState } from 'react';
import { useLabels } from '@/lib/i18n';
import { useVideoPlayer } from '@/features/video-output/contexts/VideoPlayerContext';
import { QuoteLine } from '@/features/video-output/components/output/interactive/QuoteLine';
import { Badge } from '@/components/vie/data/Badge';
import { ScoreRing } from '@/components/vie/data/ScoreRing';
import { SeekChip } from '@/components/vie/data/SeekChip';
import { CollapsibleSection } from './CollapsibleSection';
import { HeroWorthIt } from './HeroWorthIt';
import type { HeroVerdictData } from '@/features/video-output/lib/hero-verdict';

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

/** The reviewer's call as a tone pill beside the row label, so a collapsed row still says it. */
function VerdictBadge({ badge }: { badge: string }) {
  return (
    <Badge variant={BADGE_TONE[badge] ?? 'muted'} className="py-0 text-[0.6875rem] font-semibold uppercase tracking-wider">
      {badge.replace(/_/g, ' ')}
    </Badge>
  );
}

interface VerdictStatementProps {
  verdict: HeroVerdictData;
  onSeek: (seconds: number) => void;
}

/** The bottom line with a ▶ chip to where it is said; a score ring only when
 *  the reviewer gave a score at that moment (D13 — never derived). */
function VerdictStatement({ verdict, onSeek }: VerdictStatementProps) {
  const t = useLabels();
  const { bottomLine, timestamp, score, maxScore = 10 } = verdict;
  return (
    <div className="flex items-start gap-4">
      {score !== undefined && <ScoreRing score={score} total={maxScore} label={t.score} size="sm" />}
      <p className="min-w-0 max-w-[68ch] text-sm font-medium leading-relaxed text-foreground/90 text-pretty">
        {bottomLine}
        {timestamp !== undefined && <SeekChip seconds={timestamp} onSeek={onSeek} className="ms-1.5 align-baseline" />}
      </p>
    </div>
  );
}

/** A review's verdict in the hero (output-quality 4.3, Kfir 2026-10-22: the
 *  hero only): a "Verdict" row with the badge, the bottom line, its moment and
 *  the reviewer's words, then Best for / Not for in place of Worth it / Skip
 *  if. Open on desktop, collapsed on phones like the rows above it. */
export const HeroVerdict = memo(function HeroVerdict({ verdict, worthItFor, skipIf, isPhone }: HeroVerdictProps) {
  const t = useLabels();
  const { seekTo } = useVideoPlayer();
  // A manual toggle owns the open state from then on.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? !isPhone;
  const toggle = useCallback(() => setUserOpen((prev) => !(prev ?? !isPhone)), [isPhone]);
  return (
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
        </div>
        <HeroWorthIt worthItFor={worthItFor} skipIf={skipIf} bestFor={verdict.bestFor} notFor={verdict.notFor} />
      </div>
    </CollapsibleSection>
  );
});
