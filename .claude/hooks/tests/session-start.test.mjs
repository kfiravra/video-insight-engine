import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { STATE_ROOT, initGitRepo, rmrf, runHook, tmpDir } from './helpers.mjs';

const repo = tmpDir('vie-ss-');
initGitRepo(repo);
const env = { CLAUDE_PROJECT_DIR: repo };
const off = path.join(STATE_ROOT, 'guard-off');
const start = () => runHook('session-start.sh', { session_id: 's', source: 'startup' }, { env });

after(() => {
  rmrf(repo);
  fs.rmSync(off, { force: true });
});

describe('session-start', () => {
  test('prints the branch and dirty count, nothing about the guard when it is on', () => {
    fs.rmSync(off, { force: true });
    const r = start();
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /^branch main, 0 dirty files/m);
    assert.doesNotMatch(r.stdout, /git guard/);
  });

  test('says "git guard: OFF" while state/guard-off exists', () => {
    fs.writeFileSync(off, '');
    const r = start();
    assert.match(r.stdout, /^git guard: OFF/m);
    assert.ok(fs.existsSync(off), 'a fresh guard-off stays');
  });

  test('a guard-off older than a day is removed and announced (the safe direction is on)', () => {
    fs.writeFileSync(off, '');
    const old = new Date(Date.now() - 2 * 24 * 3600 * 1000);
    fs.utimesSync(off, old, old);
    const r = start();
    assert.match(r.stdout, /re-enabled/);
    assert.doesNotMatch(r.stdout, /^git guard: OFF/m);
    assert.equal(fs.existsSync(off), false);
  });

  test('lists active task folders with their Next entry', () => {
    fs.mkdirSync(path.join(repo, 'dev', 'active', 'demo-task'), { recursive: true });
    fs.writeFileSync(path.join(repo, 'dev', 'active', 'demo-task', 'notes.md'), '# demo\n\n## Next\n\nwire the route\n');
    const r = start();
    assert.match(r.stdout, /demo-task: wire the route/);
  });
});
