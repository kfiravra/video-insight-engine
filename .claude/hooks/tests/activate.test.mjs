import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { INFRA_DIR, initGitRepo, rmrf, tmpDir } from './helpers.mjs';

// A throwaway repo with a minimal old `.claude/` and a minimal `.claude2/` that
// carries the real activate.sh. Runs with --force because the tests run inside
// a Claude Code session.
function makeRepo() {
  const repo = tmpDir('vie-activate-');
  initGitRepo(repo);
  fs.mkdirSync(path.join(repo, '.claude'));
  fs.writeFileSync(path.join(repo, '.claude', 'settings.json'), '{"old":true}\n');
  fs.writeFileSync(path.join(repo, '.claude', 'settings.local.json'), '{"local":"KEEP ME"}\n');
  fs.mkdirSync(path.join(repo, '.claude2', 'hooks', 'tests'), { recursive: true });
  fs.copyFileSync(path.join(INFRA_DIR, 'activate.sh'), path.join(repo, '.claude2', 'activate.sh'));
  fs.writeFileSync(path.join(repo, '.claude2', 'settings.json'), '{"new":true}\n');
  fs.writeFileSync(path.join(repo, '.claude2', 'mcp.json'), '{"mcpServers":{}}\n');
  fs.writeFileSync(path.join(repo, '.claude2', 'hooks', 'tests', 'ok.test.mjs'),
    "import { test } from 'node:test'; test('ok', () => {});\n");
  fs.writeFileSync(path.join(repo, '.mcp.json'), '{"root":true}\n');
  return repo;
}

function activate(repo, script, ...args) {
  const env = { ...process.env };
  delete env.CLAUDECODE;
  return spawnSync('bash', [path.join(repo, script), '--force', ...args], { cwd: repo, encoding: 'utf8', env });
}

describe('activate.sh', () => {
  const repo = makeRepo();
  after(() => rmrf(repo));

  test('--dry-run changes nothing and lists the settings.local.json copy', () => {
    const r = activate(repo, '.claude2/activate.sh', '--dry-run');
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /settings\.local\.json/);
    assert.ok(fs.existsSync(path.join(repo, '.claude2', 'activate.sh')));
    assert.equal(fs.existsSync(path.join(repo, '.claude1')), false);
  });

  // 2026-11-01 comparison, §4.2 item 9: the copy was gated on `.claude/settings.local.json`
  // after the renames, when that name already pointed at the new tree, so it never ran.
  test('swap in copies settings.local.json from the old tree (2026-11-01 post-rename condition)', () => {
    const r = activate(repo, '.claude2/activate.sh');
    assert.equal(r.status, 0, r.stderr + r.stdout);
    assert.ok(fs.existsSync(path.join(repo, '.claude1', 'settings.json')));
    assert.equal(fs.readFileSync(path.join(repo, '.claude', 'settings.json'), 'utf8'), '{"new":true}\n');
    assert.equal(fs.readFileSync(path.join(repo, '.claude', 'settings.local.json'), 'utf8'), '{"local":"KEEP ME"}\n');
    assert.equal(fs.readFileSync(path.join(repo, '.claude1', 'mcp.root.json'), 'utf8'), '{"root":true}\n');
    assert.equal(fs.readFileSync(path.join(repo, '.mcp.json'), 'utf8'), '{"mcpServers":{}}\n');
    assert.match(fs.readFileSync(path.join(repo, '.git', 'info', 'exclude'), 'utf8'), /\/\.claude1\//);
  });

  test('--revert restores the old tree byte for byte and the root .mcp.json', () => {
    const r = activate(repo, '.claude/activate.sh', '--revert');
    assert.equal(r.status, 0, r.stderr + r.stdout);
    assert.equal(fs.readFileSync(path.join(repo, '.claude', 'settings.json'), 'utf8'), '{"old":true}\n');
    assert.equal(fs.readFileSync(path.join(repo, '.claude', 'settings.local.json'), 'utf8'), '{"local":"KEEP ME"}\n');
    assert.equal(fs.readFileSync(path.join(repo, '.mcp.json'), 'utf8'), '{"root":true}\n');
    assert.equal(fs.existsSync(path.join(repo, '.claude1')), false);
    assert.ok(fs.existsSync(path.join(repo, '.claude2', 'settings.local.json')), 'the copy travels back with the new tree');
    assert.doesNotMatch(fs.readFileSync(path.join(repo, '.git', 'info', 'exclude'), 'utf8'), /\/\.claude1\//);
  });

  test('refuses to swap in twice', () => {
    assert.equal(activate(repo, '.claude2/activate.sh').status, 0);
    const again = spawnSync('bash', [path.join(repo, '.claude2', 'activate.sh'), '--force'], { cwd: repo, encoding: 'utf8' });
    assert.notEqual(again.status, 0);
    activate(repo, '.claude/activate.sh', '--revert');
  });
});
