import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const HOOKS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const INFRA_DIR = path.resolve(HOOKS_DIR, '..');
export const REPO_ROOT = path.resolve(INFRA_DIR, '..');

// Every test process gets its own state root. Before 2026-11-02 the tests wrote
// into the live state/ dir and could consume a real session's git-override.
export const STATE_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'vie-state-'));
process.on('exit', () => fs.rmSync(STATE_ROOT, { recursive: true, force: true }));

export function runScript(script, input, { env = {}, cwd = HOOKS_DIR, args = [] } = {}) {
  const r = spawnSync('bash', [path.join(HOOKS_DIR, script), ...args], {
    input: input === undefined ? '' : JSON.stringify(input),
    cwd,
    encoding: 'utf8',
    env: { ...process.env, VIE_STATE_ROOT: STATE_ROOT, ...env },
    timeout: 60000,
  });
  return {
    status: r.status,
    stdout: (r.stdout || '').trim(),
    stderr: (r.stderr || '').trim(),
  };
}

export function runHook(script, input, opts) {
  return runScript(script, input, opts);
}

export function tmpDir(prefix = 'vie-hook-') {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

export function rmrf(p) {
  fs.rmSync(p, { recursive: true, force: true });
}

export function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

// A PATH with the usual tools but without jq, for the fail-closed tests.
export function pathWithoutJq() {
  const bin = tmpDir('vie-nojq-');
  const tools = ['bash', 'cat', 'tr', 'sed', 'grep', 'awk', 'mkdir', 'rm', 'dirname', 'basename',
    'env', 'sort', 'date', 'sha1sum', 'cut', 'stat', 'git', 'timeout', 'wc', 'find', 'head', 'ls', 'readlink'];
  for (const t of tools) {
    const r = spawnSync('bash', ['-c', `command -v ${t}`], { encoding: 'utf8' });
    const target = (r.stdout || '').trim();
    if (target) fs.symlinkSync(target, path.join(bin, t));
  }
  return bin;
}

export function initGitRepo(dir) {
  spawnSync('git', ['init', '-q', '-b', 'main'], { cwd: dir, encoding: 'utf8' });
  spawnSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'init'], {
    cwd: dir, encoding: 'utf8',
  });
}
