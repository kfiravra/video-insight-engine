import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { STATE_ROOT, parseJson, pathWithoutJq, rmrf, runHook, tmpDir } from './helpers.mjs';

// A project dir without .claude1 so results never depend on the real repo state.
// It holds one real directory and one real file so the checkout-<path> rule has
// something to find.
const project = tmpDir();
fs.mkdirSync(path.join(project, 'api', 'src'), { recursive: true });
fs.writeFileSync(path.join(project, 'api', 'src', 'index.ts'), '');
const env = { CLAUDE_PROJECT_DIR: project };
const run = (command, extraEnv = {}) =>
  runHook('git-guard.sh', { tool_input: { command } }, { env: { ...env, ...extraEnv } });

after(() => rmrf(project));

const BLOCKED = [
  'git stash',
  'git stash pop',
  'git stash drop',
  'cd /somewhere && git stash',
  'git reset --hard HEAD~1',
  'git reset --hard origin/main',
  'git checkout -- apps/web/src/index.css',
  'git checkout .',
  'git checkout main -- file.ts',
  'git clean -fd',
  'git push --force origin dev-2',
  'git push -f',
  'git push origin main --force-with-lease',
  'git branch -D main',
  'git restore apps/web/src/index.css',
  'git restore --worktree file.ts',
  'git restore --staged --worktree file.ts',
  'git restore -s HEAD~1 file.ts',
  'git "stash"',
  "git stas'h' pop",
  'git reset "--hard" HEAD~1',
  'git -C . stash',
  'git -C /some/dir stash pop',
  'GIT_DIR=.git git stash',
  'true; git stash',
  'echo hi | tee log && git reset --hard',
];

const ALLOWED = [
  'git status',
  'git diff HEAD',
  'git log --oneline -5',
  'git show HEAD:apps/web/src/index.css',
  'git stash list',
  'git stash show -p stash@{0}',
  'git checkout -b feature/new-branch',
  'git checkout dev-2',
  'git checkout -B hotfix origin/hotfix',
  'git checkout --orphan gh-pages',
  'git reset HEAD file.ts',
  'git reset --soft HEAD~1',
  'git branch -D feature/old-branch',
  'git add -A && git commit -m "feat: something"',
  'git commit -m "feat: x"',
  'npm test',
  'echo "git stash is banned" > note.txt',
  'git restore --staged file.ts',
  'git commit -m "docs: explain why reset --hard is banned"',
  'git switch main',
  'git worktree remove ../vie-old',
  'git -c core.pager=cat log -3',
];

const ASK = [
  'git push origin dev-2',
  'git -C api push',
  'git rebase main',
  'gh pr create --fill',
  'env -u GITHUB_TOKEN gh pr create --fill',
  'gh pr merge 12 --squash',
  'docker compose restart vie-summarizer-worker',
  'docker compose up -d --build vie-api',
  'docker compose up -d --force-recreate vie-summarizer',
  'docker-compose down',
  'docker restart vie-api',
  'cd /x && docker compose stop vie-web',
  'docker compose rm -f vie-redis',
];

const NO_ASK = [
  'git status',
  'git log --oneline',
  'npm test',
  'git show HEAD:file.ts',
  'docker compose ps',
  'docker compose logs -f vie-api',
  'docker run --rm -it node:20 node -v',
  'docker compose up -d',
  'gh pr view 12',
  'gh pr list',
  'git commit -m "feat: x"',
  'git add -A',
];

describe('git-guard blocks working-tree-destructive commands', () => {
  for (const cmd of BLOCKED) {
    test(`blocks: ${cmd}`, () => {
      const r = run(cmd);
      assert.equal(r.status, 2, r.stderr);
      assert.match(r.stderr, /BLOCKED by git-guard/);
    });
  }
});

describe('git-guard allows ordinary commands', () => {
  for (const cmd of ALLOWED) {
    test(`allows: ${cmd}`, () => {
      const r = run(cmd);
      assert.equal(r.status, 0, r.stderr);
    });
  }
});

describe('git-guard asks for push, rebase, PRs and container recreates', () => {
  for (const cmd of ASK) {
    test(`asks: ${cmd}`, () => {
      const r = run(cmd);
      assert.equal(r.status, 0, r.stderr);
      const out = parseJson(r.stdout);
      assert.ok(out, `expected JSON on stdout, got: ${r.stdout}`);
      assert.equal(out.hookSpecificOutput.permissionDecision, 'ask');
      assert.ok(out.hookSpecificOutput.permissionDecisionReason.length > 10);
    });
  }
  for (const cmd of NO_ASK) {
    test(`does not ask: ${cmd}`, () => {
      const r = run(cmd);
      assert.equal(r.status, 0);
      assert.equal(r.stdout, '');
    });
  }
});

// 2026-11-01 comparison, §4.2 item 1: the stash list|show and restore --staged
// exemptions were tested against the whole line, so one harmless segment
// whitelisted a destructive one.
describe('git-guard checks each command segment on its own (2026-11-01 whole-line exemption)', () => {
  for (const cmd of [
    'git stash list; git stash pop',
    'git stash show && git stash drop',
    'git stash list || git stash',
    'git restore --staged x && git restore y',
    'git restore --staged a.ts; git restore b.ts',
    'git stash list | cat; git stash pop',
  ]) {
    test(`blocks: ${cmd}`, () => {
      assert.equal(run(cmd).status, 2);
    });
  }
  test('asks when only a later segment pushes: git stash list && git push', () => {
    const r = run('git stash list && git push');
    assert.equal(r.status, 0);
    assert.equal(parseJson(r.stdout)?.hookSpecificOutput.permissionDecision, 'ask');
  });
});

// 2026-11-01 comparison, §4.2 item 2: quoted strings were stripped before matching,
// so wrapping the command in an interpreter hid it.
describe('git-guard scans quoted text when an interpreter runs it (2026-11-01 bash -c bypass)', () => {
  for (const cmd of [
    'bash -c "git reset --hard"',
    "sh -c 'git clean -fd'",
    'zsh -c "cd api && git stash"',
    'eval "git stash pop"',
    'echo "git stash" | xargs -I{} bash -c "{}"',
    "bash <<'EOF'\ngit reset --hard\nEOF",
  ]) {
    test(`blocks: ${cmd.replace(/\n/g, '\\n')}`, () => {
      assert.equal(run(cmd).status, 2);
    });
  }
  test('still allows quoted mentions without an interpreter: echo "bash -c git stash is bad"', () => {
    // "bash" appears inside the quotes only; the command itself is echo.
    assert.equal(run('echo "please never run: git stash"').status, 0);
  });
});

// 2026-09-02 memory and 2026-11-02 live: a heredoc whose prose mentioned the guard
// and the word clean was blocked as `git clean`, and `git add clean.sh` as well.
describe('git-guard ignores prose and path names (2026-09-02 heredoc false block)', () => {
  for (const cmd of [
    "cat >> report.md <<'EOF'\nThe guard blocks git clean and git stash.\nEOF",
    "python3 - <<'EOF'\nprint('git reset --hard is banned')\nEOF",
    'git status && make clean',
    'git add clean.sh',
    'git add hooks/clean-state.sh && git commit -m "chore: add clean script"',
    'pnpm clean && git status',
    'npm run clean',
    'echo done > restore.log',
  ]) {
    test(`allows: ${cmd.replace(/\n/g, '\\n')}`, () => {
      const r = run(cmd);
      assert.equal(r.status, 0, r.stderr);
    });
  }
  test('a heredoc fed to bash is still read: cat <<EOF is prose, bash <<EOF is a command', () => {
    assert.equal(run("cat <<'EOF'\ngit stash\nEOF").status, 0);
    assert.equal(run("bash <<'EOF'\ngit stash\nEOF").status, 2);
  });
});

// 2026-11-01 comparison, §4.2 item 5: destructive forms the pattern list did not know.
describe('git-guard knows the other ways to discard work (2026-11-01 probe gaps)', () => {
  for (const cmd of [
    'git checkout HEAD file.ts',
    'git checkout main api/src/index.ts',
    'git checkout api',
    'git checkout api/src/index.ts',
    'git checkout -f main',
    'git checkout --force dev-2',
    'git switch --discard-changes main',
    'git switch -f main',
    'git reset --merge',
    'git reset --keep HEAD~1',
    'git branch --delete --force main',
    'git branch -df main',
    'git branch -d master',
    'git push origin +main',
    'git push origin --delete main',
    'git push origin :main',
    'git push --force-if-includes',
    'git worktree remove --force ../vie-x',
    'git worktree remove -f ../vie-x',
    'git update-ref -d refs/heads/main',
  ]) {
    test(`blocks: ${cmd}`, () => {
      assert.equal(run(cmd).status, 2);
    });
  }
  test('a branch name that is not a path is still a plain switch: git checkout feat/billing-roles', () => {
    assert.equal(run('git checkout feat/billing-roles').status, 0);
  });
});

describe('git-guard edge inputs', () => {
  test('non-Bash input with no command is allowed silently', () => {
    const r = runHook('git-guard.sh', { tool_input: { file_path: '/x.ts' } }, { env });
    assert.equal(r.status, 0);
    assert.equal(r.stdout, '');
  });
  test('empty stdin is allowed', () => {
    const r = runHook('git-guard.sh', undefined, { env });
    assert.equal(r.status, 0);
  });
});

describe('git-guard swap guard while .claude1/ exists', () => {
  const swapped = tmpDir();
  fs.mkdirSync(path.join(swapped, '.claude1'));
  const swapEnv = { CLAUDE_PROJECT_DIR: swapped };
  after(() => rmrf(swapped));

  for (const cmd of ['git add -A', 'git add .', 'git add --all', 'git add -u', 'git commit -am "x"', 'git commit -a -m "x"']) {
    test(`blocks during swap: ${cmd}`, () => {
      const r = run(cmd, swapEnv);
      assert.equal(r.status, 2);
      assert.match(r.stderr, /infra swap in progress/);
    });
  }
  for (const cmd of ['git add api/src/x.ts', 'git commit -m "feat: x"', 'git status']) {
    test(`still allows during swap: ${cmd}`, () => {
      assert.equal(run(cmd, swapEnv).status, 0);
    });
  }
  test('the swap guard stays on while /guard off is active', () => {
    fs.writeFileSync(path.join(STATE_ROOT, 'guard-off'), '');
    try {
      assert.equal(run('git add -A', swapEnv).status, 2);
    } finally {
      fs.rmSync(path.join(STATE_ROOT, 'guard-off'), { force: true });
    }
  });
});

describe('git-guard override file', () => {
  const override = path.join(STATE_ROOT, 'git-override');
  test('allows one blocked command, then is consumed', () => {
    fs.mkdirSync(path.dirname(override), { recursive: true });
    fs.writeFileSync(override, '');
    const first = run('git stash');
    assert.equal(first.status, 0, first.stderr);
    assert.equal(fs.existsSync(override), false);
    const second = run('git stash');
    assert.equal(second.status, 2);
  });
});

describe('git-guard honours /guard off', () => {
  const off = path.join(STATE_ROOT, 'guard-off');
  after(() => fs.rmSync(off, { force: true }));
  test('blocked and asked commands run unprompted while state/guard-off exists', () => {
    fs.writeFileSync(off, '');
    assert.equal(run('git stash').status, 0);
    assert.equal(run('git reset --hard').status, 0);
    const push = run('git push origin dev-2');
    assert.equal(push.status, 0);
    assert.equal(push.stdout, '');
  });
  test('removing the file (guard on) restores the block', () => {
    fs.rmSync(off, { force: true });
    assert.equal(run('git stash').status, 2);
  });
  test('guard-ctl.sh off/on writes and removes the file and says so', () => {
    const offR = runHook('guard-ctl.sh', undefined, { env, args: ['off'] });
    assert.match(offR.stdout, /git guard: OFF/);
    assert.ok(fs.existsSync(off));
    assert.match(runHook('guard-ctl.sh', undefined, { env }).stdout, /OFF since/);
    const onR = runHook('guard-ctl.sh', undefined, { env, args: ['on'] });
    assert.match(onR.stdout, /git guard: ON/);
    assert.equal(fs.existsSync(off), false);
  });
});

// 2026-11-01 comparison, §4.2 item 7: without jq the guard parsed nothing and
// allowed everything.
describe('git-guard fails closed without jq (2026-11-01 fail-open)', () => {
  const bin = pathWithoutJq();
  after(() => rmrf(bin));
  test('every Bash command is refused with an install hint', () => {
    const r = run('git status', { PATH: bin });
    assert.equal(r.status, 2, r.stderr);
    assert.match(r.stderr, /jq is not installed/);
  });
});
