import { useState, useEffect, useRef, type RefObject } from "react";

/**
 * Whether a single-line, ellipsised element cuts its text (scrollWidth past
 * clientWidth). Re-checked on every resize, so a line that wraps when opened
 * reads as uncut and reads as cut again once it folds back. Shared by the
 * sidebar rows (tooltip on a cut title) and the hero's one-line worth-it
 * lines ("more" on a cut line).
 */
export function useIsTruncated(ref: RefObject<HTMLElement | null>): boolean {
  const [isTruncated, setIsTruncated] = useState(false);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const check = () => {
      setIsTruncated(el.scrollWidth > el.clientWidth);
    };

    check();

    const observer = new ResizeObserver(() => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        check();
      });
    });
    observer.observe(el);

    return () => {
      observer.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [ref]); // a stable RefObject: the effect runs once

  return isTruncated;
}
