import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

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
