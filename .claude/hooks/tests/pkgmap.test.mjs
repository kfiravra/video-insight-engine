import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { REPO_ROOT, rmrf, runScript, tmpDir } from './helpers.mjs';

const env = { CLAUDE_PROJECT_DIR: REPO_ROOT };

function map(...args) {
  return mapIn(REPO_ROOT, ...args);
}

function mapIn(root, ...args) {
  const r = runScript('lib/pkgmap.sh', undefined, { env: { CLAUDE_PROJECT_DIR: root }, args });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      const [label, cwd, command] = line.split('\t');
      return { label, cwd, command };
    });
}

const labels = (rows) => rows.map((r) => r.label);

describe('pkgmap maps touched paths to checks', () => {
  test('api TypeScript file -> api tsc and eslint on that file', () => {
    const rows = map('api/src/services/video.service.ts');
    assert.deepEqual(labels(rows), ['api-tsc', 'api-eslint']);
    assert.equal(rows[0].cwd, '.');
    assert.match(rows[0].command, /pnpm --filter @vie\/api exec tsc --noEmit/);
    assert.equal(rows[1].cwd, 'api');
    assert.match(rows[1].command, /eslint src\/services\/video\.service\.ts/);
  });

  test('web tsx file -> tsc -b and eslint on that file', () => {
    const rows = map('apps/web/src/App.tsx');
    assert.deepEqual(labels(rows), ['web-tsc', 'web-eslint']);
    assert.match(rows[0].command, /tsc -b/);
    assert.match(rows[1].command, /eslint src\/App\.tsx/);
  });

  test('absolute path inside the repo is accepted', () => {
    const rows = map(`${REPO_ROOT}/api/src/index.ts`);
    assert.ok(labels(rows).includes('api-tsc'));
  });

  test('shared package edit builds it first, then checks api and web', () => {
    const rows = map('packages/types/src/vie-response.ts');
    const l = labels(rows);
    assert.equal(l[0], 'types');
    assert.ok(l.indexOf('types') < l.indexOf('api-tsc'));
    assert.ok(l.includes('web-tsc'));
  });

  test('python file -> ruff with the touched file (ruff resolves in this repo)', () => {
    const rows = map('services/summarizer/src/config.py');
    assert.equal(rows.length, 1);
    assert.equal(rows[0].label, 'ruff');
    assert.match(rows[0].command, /ruff check --config ruff\.toml services\/summarizer\/src\/config\.py/);
  });

  test('python file with VIE_STOP_PYRIGHT=1 adds a pyright line for its service', () => {
    const r = runScript('lib/pkgmap.sh', undefined, {
      env: { ...env, VIE_STOP_PYRIGHT: '1' },
      args: ['services/assistant/src/server.py'],
    });
    assert.match(r.stdout, /pyright-assistant\tservices\/assistant\tnpx pyright@1\.1\.407 --project \. src\/server\.py/);
  });

  test('hook edits -> hook self-test', () => {
    const rows = map('.claude/hooks/git-guard.sh');
    assert.deepEqual(labels(rows), ['hooks']);
    assert.match(rows[0].command, /node --test .*hooks\/tests\/\*\.test\.mjs/);
  });

  test('paths outside the project and non-code files produce nothing', () => {
    assert.deepEqual(map('/outside/file.ts'), []);
    assert.deepEqual(map('docs/ARCHITECTURE.md'), []);
    assert.deepEqual(map(), []);
  });

  test('--tests adds the test runners for touched packages', () => {
    const l = labels(map('--tests', 'api/src/x.ts', 'services/admin/src/y.py'));
    assert.ok(l.includes('api-test'));
    assert.ok(l.includes('pytest-admin'));
  });

  test('--all covers every package', () => {
    const l = labels(map('--all'));
    for (const want of ['types', 'shared', 'api-tsc', 'api-eslint', 'web-tsc', 'web-eslint', 'ruff', 'hooks']) {
      assert.ok(l.includes(want), `missing ${want} in ${l}`);
    }
  });
});

// 2026-05-31: two TS6133 unused-locals passed `tsc --noEmit` against apps/web's root
// tsconfig (it has `files: []` and only references) and broke the Docker build.
// The web typecheck is `tsc -b` and nothing else.
describe('pkgmap web typecheck follows tsconfig.app.json (2026-05-31 TS6133 hollow --noEmit)', () => {
  test('the web check is tsc -b, never a bare --noEmit', () => {
    const web = map('apps/web/src/App.tsx').find((r) => r.label === 'web-tsc');
    assert.ok(web);
    assert.match(web.command, /\btsc -b\b/);
    assert.doesNotMatch(web.command, /--noEmit/);
  });
  test('apps/web/tsconfig.json really references tsconfig.app.json in this repo', () => {
    const cfg = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'apps', 'web', 'tsconfig.json'), 'utf8'));
    assert.ok(cfg.references?.some((r) => r.path === './tsconfig.app.json'), JSON.stringify(cfg));
    assert.ok(fs.existsSync(path.join(REPO_ROOT, 'apps', 'web', 'tsconfig.app.json')));
  });
  test('a tree without tsconfig.app.json gets a skip line, not a hollow check', () => {
    const root = tmpDir();
    fs.mkdirSync(path.join(root, 'apps', 'web', 'src'), { recursive: true });
    after(() => rmrf(root));
    const rows = mapIn(root, 'apps/web/src/App.tsx');
    const skip = rows.find((r) => r.label === 'skip');
    assert.ok(skip, JSON.stringify(rows));
    assert.match(skip.command, /tsconfig\.app\.json missing/);
    assert.equal(rows.find((r) => r.label === 'web-tsc'), undefined);
  });
});
