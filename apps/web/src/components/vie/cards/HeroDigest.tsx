import { HeroTakeaways } from './HeroTakeaways';
import { HeroVerdict } from './HeroVerdict';
import { HeroWorthIt, HeroWorthItRow } from './HeroWorthIt';
import type { HeroVerdictData } from '@/features/video-output/lib/hero-verdict';

interface HeroDigestProps {
  /** Each a claim closing with the `[m:ss]` where it is said (a ▶ seek chip). */
  keyTakeaways: string[];
  worthItFor?: string;
  skipIf?: string;
  /** A review's verdict, lifted out of its tab (4.3): it takes the worth-it lines' place. */
  verdict?: HeroVerdictData;
  showTakeaways: boolean;
  /** The "should I watch?" block renders: a verdict, or a worth-it line. */
  showWorth: boolean;
  isStreaming: boolean;
  isPhone: boolean;
}

interface WorthBlockProps {
  worthItFor?: string;
  skipIf?: string;
  verdict?: HeroVerdictData;
  isPhone: boolean;
}

/** A review's verdict row (whose Best for / Not for replace the lines), else
 *  the worth-it lines: open on desktop, a collapsed "Worth it?" row on phones. */
function WorthBlock({ worthItFor, skipIf, verdict, isPhone }: WorthBlockProps) {
  if (verdict) return <HeroVerdict verdict={verdict} worthItFor={worthItFor} skipIf={skipIf} isPhone={isPhone} />;
  if (isPhone) return <HeroWorthItRow worthItFor={worthItFor} skipIf={skipIf} />;
  return <HeroWorthIt worthItFor={worthItFor} skipIf={skipIf} />;
}

/** The hero's digest under the header: the takeaways, then the "Worth it for /
 *  Skip if" lines (a review's verdict in their place). Phones get a third
 *  collapsed row instead of the open lines, and rows a steady 16 px apart, so
 *  the tab panel starts within 60 % of the first screen (output-quality 4.4);
 *  desktop is unchanged. */
export function HeroDigest({
  keyTakeaways,
  worthItFor,
  skipIf,
  verdict,
  showTakeaways,
  showWorth,
  isStreaming,
  isPhone,
}: HeroDigestProps) {
  if (!showTakeaways && !showWorth) return null;
  return (
    <div className="space-y-4 px-6 pb-4 pt-4 md:space-y-5 md:px-7 md:pb-6">
      {showTakeaways && <HeroTakeaways takeaways={keyTakeaways} isStreaming={isStreaming} isPhone={isPhone} />}
      {showWorth && <WorthBlock worthItFor={worthItFor} skipIf={skipIf} verdict={verdict} isPhone={isPhone} />}
    </div>
  );
}
