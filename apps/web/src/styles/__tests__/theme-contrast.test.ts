import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

/**
 * WCAG AA contrast contract for the theme tokens in `index.css`, per shipped
 * theme: text tokens stay ≥ 4.5:1 on the surfaces and tints they are drawn on.
 * Values are parsed from the stylesheet, so a token edit that breaks a pair
 * fails here before axe finds it on a page.
 */

type Rgb = readonly [number, number, number];

interface Oklch {
  l: number;
  c: number;
  h: number;
  rgb: Rgb;
}

// Read from disk: vitest stubs CSS imports (even `?raw`) to an empty module.
const themeCss = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');

const AA_TEXT = 4.5;
const THEMES = ['light', 'dark', 'lagoon'] as const;
type ThemeName = (typeof THEMES)[number];

/** Every value `getAccentForTag` (content-accent.ts) can bind to `--vie-accent`. */
const DOMAIN_ACCENTS = ['primary', 'vie-coral', 'vie-plum', 'vie-sky', 'vie-mint', 'vie-honey', 'vie-rose'];
/** Tokens drawn as text on their own tint: the domain accents plus Badge's success variant. */
const TINTED_TEXT_TOKENS = [...DOMAIN_ACCENTS, 'success'];
/** Tinted text is drawn plain and on its own tint: SeekChip /10, done-step badge /15. */
const ACCENT_TINTS = [0, 0.05, 0.1, 0.15];
const TEXT_SURFACES = ['card', 'background'];
/** `text-muted-foreground/70` — the faintest muted text the token contract covers. */
const MUTED_TEXT_ALPHA = 0.7;
/** `--vie-accent-on-code`: the accent's lightness floor on the always-dark code surface. */
const ON_CODE_MIN_L = 0.74;
/** CodePlayground's Run button: accent text on its own /20 tint over the code surface. */
const ON_CODE_TINT = 0.2;

function oklchToSrgb(l: number, c: number, h: number): Rgb {
  const a = c * Math.cos((h * Math.PI) / 180);
  const b = c * Math.sin((h * Math.PI) / 180);
  const lc = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const mc = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const sc = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const encode = (linear: number): number => {
    const v = Math.min(1, Math.max(0, linear));
    return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
  };
  return [
    encode(4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc),
    encode(-1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc),
    encode(-0.0041960863 * lc - 0.7034186147 * mc + 1.707614701 * sc),
  ];
}

function luminance([r, g, b]: Rgb): number {
  const linear = (v: number): number => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `fg` at `alpha` painted over `bg`, the way `bg-[var(--x)]/10` composites. */
function over(fg: Rgb, bg: Rgb, alpha: number): Rgb {
  const mix = (i: number): number => fg[i] * alpha + bg[i] * (1 - alpha);
  return [mix(0), mix(1), mix(2)];
}

/** Every `--name: oklch(L% C H);` in the top-level blocks opened by `selector {`. */
function parseTokens(selector: string): Map<string, Oklch> {
  const escaped = selector.replace(/[[\]"=]/g, (ch) => `\\${ch}`);
  const tokens = new Map<string, Oklch>();
  for (const [, body] of themeCss.matchAll(new RegExp(`^${escaped} \\{([\\s\\S]*?)^\\}`, 'gm'))) {
    for (const [, name, l, c, h] of body.matchAll(/--([\w-]+):\s*oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\)\s*;/g)) {
      const lightness = Number(l) / 100;
      tokens.set(name, { l: lightness, c: Number(c), h: Number(h), rgb: oklchToSrgb(lightness, Number(c), Number(h)) });
    }
  }
  return tokens;
}

const lightTokens = parseTokens(':root');
const themeTokens: Record<ThemeName, ReadonlyMap<string, Oklch>> = {
  light: lightTokens,
  dark: new Map([...lightTokens, ...parseTokens('[data-theme="dark"]')]),
  lagoon: new Map([...lightTokens, ...parseTokens('[data-theme="lagoon"]')]),
};

function token(theme: ThemeName, name: string): Oklch {
  const value = themeTokens[theme].get(name);
  if (!value) throw new Error(`--${name} is not an oklch() token in the ${theme} theme`);
  return value;
}

/** The lowest contrast of `text` on each surface under `tint` at each alpha. */
function worstOnSurfaces(theme: ThemeName, text: Rgb, tint: Rgb, alphas: readonly number[]): number {
  const ratios = TEXT_SURFACES.flatMap((surface) =>
    alphas.map((alpha) => contrast(text, over(tint, token(theme, surface).rgb, alpha))),
  );
  return Math.min(...ratios);
}

/** `--vie-accent-foreground` (index.css): frost on dark accents (l < 0.62), ink on light ones. */
function accentForeground(accentLightness: number): Rgb {
  return oklchToSrgb(0.18 + 0.8 * Math.min(1, Math.max(0, (0.62 - accentLightness) * 100)), 0.015, 280);
}

describe('theme tokens (WCAG AA)', () => {
  describe.each(THEMES)('%s theme', (theme) => {
    it('should keep body text readable on the page and on cards', () => {
      expect(contrast(token(theme, 'foreground').rgb, token(theme, 'background').rgb)).toBeGreaterThanOrEqual(AA_TEXT);
      expect(contrast(token(theme, 'card-foreground').rgb, token(theme, 'card').rgb)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it('should keep primary-foreground readable on the primary fill', () => {
      expect(contrast(token(theme, 'primary-foreground').rgb, token(theme, 'primary').rgb)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it('should keep muted text readable when it is drawn at 70 % opacity', () => {
      const muted = token(theme, 'muted-foreground').rgb;
      const ratios = TEXT_SURFACES.map((surface) => {
        const bg = token(theme, surface).rgb;
        return contrast(over(muted, bg, MUTED_TEXT_ALPHA), bg);
      });
      expect(Math.min(...ratios)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it.each(TINTED_TEXT_TOKENS)('should keep --%s readable as text on its own tint', (accent) => {
      const { rgb } = token(theme, accent);
      expect(worstOnSurfaces(theme, rgb, rgb, ACCENT_TINTS)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it.each(DOMAIN_ACCENTS)('should keep the derived accent foreground readable on a solid --%s fill', (accent) => {
      const { l, rgb } = token(theme, accent);
      expect(contrast(accentForeground(l), rgb)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it.each(DOMAIN_ACCENTS)('should keep --%s readable on the code surface after the on-code lift', (accent) => {
      const { l, c, h } = token(theme, accent);
      const lifted = oklchToSrgb(Math.max(l, ON_CODE_MIN_L), c, h);
      expect(contrast(lifted, over(lifted, token(theme, 'code-bg').rgb, ON_CODE_TINT))).toBeGreaterThanOrEqual(AA_TEXT);
    });
  });

  /** The declarations of every top-level rule whose selector list ends with `selectors`. */
  function ruleBody(selectors: string): string {
    const escaped = selectors.replace(/[[\].-]/g, (ch) => `\\${ch}`);
    const rules = themeCss.matchAll(new RegExp(`^${escaped} \\{([\\s\\S]*?)^\\}`, 'gm'));
    return [...rules].map(([, body]) => body).join('\n');
  }

  it('should re-derive the accent foreground and ink where --vie-accent is re-bound', () => {
    const derived = ruleBody('[data-domain],\n.bg-code-bg');
    expect(derived).toMatch(/--vie-accent-foreground:\s*oklch\(\s*from var\(--vie-accent\)/);
    expect(derived).toMatch(/--vie-accent-ink:\s*oklch\(from var\(--vie-accent\)/);
  });

  it('should bind the accent to its on-code lift on the code surface', () => {
    expect(ruleBody('[data-domain]')).toMatch(/--vie-accent-on-code:\s*oklch\(from var\(--vie-accent\) max\(l, 0\.74\)/);
    expect(ruleBody('.bg-code-bg')).toMatch(/--vie-accent:\s*var\(--vie-accent-on-code\)/);
  });
});
