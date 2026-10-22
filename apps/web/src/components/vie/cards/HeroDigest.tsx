import { HeroTakeaways } from './HeroTakeaways';
import { HeroVerdict } from './HeroVerdict';
import { HeroWorthIt } from './HeroWorthIt';
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
 *  the worth-it lines. */
function WorthBlock({ worthItFor, skipIf, verdict, isPhone }: WorthBlockProps) {
  if (verdict) return <HeroVerdict verdict={verdict} worthItFor={worthItFor} skipIf={skipIf} isPhone={isPhone} />;
  return <HeroWorthIt worthItFor={worthItFor} skipIf={skipIf} />;
}

/** The hero's digest under the header: the takeaways, then the "Worth it for /
 *  Skip if" lines (a review's verdict in their place). Phones keep the worth-it
 *  lines below the (collapsed) takeaways: two short lines still leave the tab
 *  strip on the first screen. */
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
    <div className="space-y-5 px-6 pb-5 md:px-7 md:pb-6 pt-4">
      {showTakeaways && <HeroTakeaways takeaways={keyTakeaways} isStreaming={isStreaming} isPhone={isPhone} />}
      {showWorth && <WorthBlock worthItFor={worthItFor} skipIf={skipIf} verdict={verdict} isPhone={isPhone} />}
    </div>
  );
}
