import { useEffect, useState } from 'react';

export interface VieFlowTheme {
  isDark: boolean;
  accent: string;
}

const DARK_THEMES = new Set(['dark', 'lagoon']);

function readTheme(): VieFlowTheme {
  if (typeof document === 'undefined') {
    return { isDark: false, accent: 'var(--vie-accent)' };
  }
  const root = document.documentElement;
  // ThemeProvider sets data-theme, never a .dark class; lagoon is a dark-family
  // theme (same set as the `dark` custom variant in index.css).
  const isDark = DARK_THEMES.has(root.dataset.theme ?? '');
  const accent =
    getComputedStyle(root).getPropertyValue('--vie-accent').trim() ||
    'var(--vie-accent)';
  return { isDark, accent };
}

/**
 * Reads the VIE accent + dark-mode signal off the document root so node
 * components can colour themselves consistently with the rest of the app.
 * Safe outside a browser context — returns sensible defaults.
 */
export function useVieFlowTheme(): VieFlowTheme {
  // The initializer reads the theme at mount; the observer follows every later change.
  const [theme, setTheme] = useState<VieFlowTheme>(readTheme);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const observer = new MutationObserver(() => setTheme(readTheme()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class', 'style'],
    });
    return () => observer.disconnect();
  }, []);

  return theme;
}
