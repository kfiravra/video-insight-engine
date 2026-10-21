import { HeroTakeaways } from './HeroTakeaways';
import { HeroWorthIt } from './HeroWorthIt';

interface HeroDigestProps {
  /** Each a claim closing with the `[m:ss]` where it is said (a ▶ seek chip). */
  keyTakeaways: string[];
  worthItFor?: string;
  skipIf?: string;
  showTakeaways: boolean;
  showWorth: boolean;
  isStreaming: boolean;
  isPhone: boolean;
}

/** The hero's digest under the header: the takeaways, then the "Worth it for /
 *  Skip if" lines. Phones keep the worth-it lines below the (collapsed)
 *  takeaways: two short lines still leave the tab strip on the first screen. */
export function HeroDigest({
  keyTakeaways,
  worthItFor,
  skipIf,
  showTakeaways,
  showWorth,
  isStreaming,
  isPhone,
}: HeroDigestProps) {
  if (!showTakeaways && !showWorth) return null;
  return (
    <div className="space-y-5 px-6 pb-5 md:px-7 md:pb-6 pt-4">
      {showTakeaways && <HeroTakeaways takeaways={keyTakeaways} isStreaming={isStreaming} isPhone={isPhone} />}
      {showWorth && <HeroWorthIt worthItFor={worthItFor} skipIf={skipIf} />}
    </div>
  );
}
