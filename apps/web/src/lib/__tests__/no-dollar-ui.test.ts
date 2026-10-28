import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import ts from 'typescript';

// Users are billed in minutes. The dollar-era usage UI (daily USD limit, "$1.25
// of $2.00", "Upgrade to Pro for $20/day") is gone, and nothing may bring it back.
// This gate reads every source file under apps/web/src, tests included.

const SRC_ROOT = resolve(__dirname, '../..');
const THIS_FILE = resolve(__dirname, 'no-dollar-ui.test.ts');

const FORBIDDEN: Array<{ name: string; pattern: RegExp }> = [
  { name: 'limitUsd', pattern: /limitUsd/ },
  { name: 'the DAILY_LIMIT_REACHED code', pattern: /DAILY_LIMIT_REACHED/ },
  { name: '"Upgrade to Pro for" copy', pattern: /Upgrade to Pro for/ },
  { name: 'formatUsd', pattern: /formatUsd/ },
  { name: 'template-literal dollar formatting', pattern: /\$\$\{/ },
  { name: 'dollar-sign string concatenation', pattern: /['"`]\$['"`]\s*\+/ },
];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (entry === 'node_modules') return [];
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry) && path !== THIS_FILE ? [path] : [];
  });
}

describe('no dollar amounts in the web app', () => {
  const files = sourceFiles(SRC_ROOT).map((path) => ({
    name: relative(SRC_ROOT, path),
    text: readFileSync(path, 'utf8'),
  }));

  it('should scan the source tree', () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it.each(FORBIDDEN)('should not contain $name anywhere in apps/web/src', ({ pattern }) => {
    const offenders = files.filter((file) => pattern.test(file.text)).map((file) => file.name);

    expect(offenders).toEqual([]);
  });
});

// ─── The strict rule for the billing surfaces ───
//
// The whole-tree rule above catches the dollar-era code by name. This one is
// stricter and scoped to the files that show a user their minutes: no string
// they render may hold a `$`, `Usd` or `currency`, and no identifier in them
// may be named after money. It reads the code as code (TypeScript's parser), so
// the `${…}` of a template literal and a regex's `$` anchor are not mistaken for
// a dollar sign, but every string, template text and JSX text is checked.

const MONEY = /\$|Usd|currency/i;
const MONEY_IDENTIFIER = /Usd|currency/i;

function isInTests(path: string): boolean {
  return /(^|\/)__tests__\//.test(path) || /\.test\.(ts|tsx)$/.test(path);
}

/** Money-like text found in `source`, as `kind: text` lines (empty when clean). */
function moneyFindings(source: string, fileName = 'file.tsx'): string[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const findings: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node)
      || ts.isTemplateMiddle(node) || ts.isTemplateTail(node) || ts.isJsxText(node)) {
      if (MONEY.test(node.text)) findings.push(`text: ${node.text.trim()}`);
    } else if (ts.isIdentifier(node) && MONEY_IDENTIFIER.test(node.text)) {
      findings.push(`name: ${node.text}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return findings;
}

/** Only the object assigned to `billing:` in an i18n table. */
function billingBlock(source: string): string {
  const file = ts.createSourceFile('table.ts', source, ts.ScriptTarget.Latest, true);
  let block = '';
  const visit = (node: ts.Node): void => {
    if (ts.isPropertyAssignment(node) && node.name.getText(file) === 'billing') block = node.initializer.getText(file);
    else ts.forEachChild(node, visit);
  };
  visit(file);
  return block;
}

function billingSurfaceFiles(): string[] {
  const inScope = (path: string): boolean => {
    const name = relative(SRC_ROOT, path).split('\\').join('/');
    if (isInTests(name)) return false;
    return (
      name.startsWith('components/usage/') ||
      name === 'components/playlists/PlaylistImportSummary.tsx' ||
      name === 'pages/BillingPage.tsx' ||
      name.startsWith('lib/billing-') ||
      name === 'lib/format-minutes.ts' ||
      name === 'lib/free-video.ts' ||
      name === 'hooks/use-billing-text.ts' ||
      name === 'api/usage.ts'
    );
  };
  return sourceFiles(SRC_ROOT).filter(inScope);
}

describe('the checker for money in billing surfaces', () => {
  it.each([
    ['a dollar amount in a string', 'const a = "You owe $5";', 'text: You owe $5'],
    ['a dollar sign in JSX text', 'const a = <p>Pay $5</p>;', 'text: Pay $5'],
    ['a dollar sign in template text', 'const a = `cost: $${n}`;', 'text: cost: $'],
    ['the word currency in a string', "const a = 'Choose a currency';", 'text: Choose a currency'],
    ['Usd in a string', 'const a = "limitUsd";', 'text: limitUsd'],
    ['an identifier named for dollars', 'const costUsd = 1;', 'name: costUsd'],
    ['an identifier named for currency', 'function formatCurrency() {}', 'name: formatCurrency'],
  ])('should flag %s', (_name, source, expected) => {
    expect(moneyFindings(source)).toContain(expected);
  });

  it.each([
    ['a template literal substitution', 'const a = `${count} minutes`;'],
    ['a regex end anchor', 'const a = /^[A-Z]+$/;'],
    ['a regex with a dollar and a group', 'const a = /(foo|bar)$/i;'],
    ['plain minutes copy', 'const a = <p>412 of 600 minutes left</p>;'],
  ])('should not flag %s', (_name, source) => {
    expect(moneyFindings(source)).toEqual([]);
  });
});

describe('no money on the billing surfaces', () => {
  it('should cover the billing components, page and libraries', () => {
    const names = billingSurfaceFiles().map((path) => relative(SRC_ROOT, path).split('\\').join('/'));

    expect(names).toEqual(
      expect.arrayContaining([
        'components/usage/UsageTile.tsx',
        'components/usage/MinutesCallout.tsx',
        'pages/BillingPage.tsx',
        'lib/billing-errors.ts',
        'lib/billing-notice-text.ts',
      ]),
    );
  });

  it('should hold no dollar sign, Usd or currency in any rendered string or name', () => {
    const offenders = billingSurfaceFiles().flatMap((path) =>
      moneyFindings(readFileSync(path, 'utf8'), path).map((finding) => `${relative(SRC_ROOT, path)} ${finding}`),
    );

    expect(offenders).toEqual([]);
  });

  it.each(['en', 'he', 'ar'])('should hold no dollar sign, Usd or currency in the %s billing labels', (language) => {
    const block = billingBlock(readFileSync(resolve(SRC_ROOT, `lib/i18n/${language}.ts`), 'utf8'));

    expect(block.length).toBeGreaterThan(1000);
    expect(moneyFindings(`const labels = ${block};`, `${language}.ts`)).toEqual([]);
  });
});
