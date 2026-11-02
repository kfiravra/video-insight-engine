import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { STATE_ROOT, rmrf, runHook, tmpDir } from './helpers.mjs';

const project = tmpDir();
for (const f of ['api/src/a.ts', 'api/src/b.ts', 'services/summarizer/src/c.py', 'docs/notes.md']) {
  fs.mkdirSync(path.dirname(path.join(project, f)), { recursive: true });
  fs.writeFileSync(path.join(project, f), '');
}
after(() => rmrf(project));

let counter = 0;
function touchedAfter(input) {
  const sid = `pe-${process.pid}-${++counter}`;
  const r = runHook('post-edit.sh', { session_id: sid, ...input }, { env: { CLAUDE_PROJECT_DIR: project } });
  assert.equal(r.status, 0, r.stderr);
  const f = path.join(STATE_ROOT, sid, 'touched');
  return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean).sort() : [];
}

describe('post-edit records Edit/Write targets', () => {
  test('a file inside the project is recorded relative to the root', () => {
    assert.deepEqual(touchedAfter({ tool_name: 'Edit', tool_input: { file_path: `${project}/api/src/a.ts` } }), ['api/src/a.ts']);
  });
  test('a file outside the project (auto-memory, scratchpad) is ignored', () => {
    assert.deepEqual(touchedAfter({ tool_name: 'Write', tool_input: { file_path: '/elsewhere/memory.md' } }), []);
  });
});

// 2026-11-01 comparison, §4.2 item 3: in bypass mode the harness edits through
// sed and heredocs; the Edit|Write matcher never fired and the Stop check saw nothing.
describe('post-edit records files written through Bash (2026-11-01 bypass-mode edits invisible)', () => {
  test('sed -i target', () => {
    assert.deepEqual(
      touchedAfter({ tool_name: 'Bash', cwd: project, tool_input: { command: "sed -i 's/a/b/' api/src/a.ts" } }),
      ['api/src/a.ts'],
    );
  });
  test('sed -i with several files and a cwd inside the project', () => {
    assert.deepEqual(
      touchedAfter({ tool_name: 'Bash', cwd: `${project}/api`, tool_input: { command: 'sed -i -e "s/x/y/" src/a.ts src/b.ts' } }),
      ['api/src/a.ts', 'api/src/b.ts'],
    );
  });
  test('redirect and append targets, including heredoc writes', () => {
    assert.deepEqual(
      touchedAfter({ tool_name: 'Bash', cwd: project, tool_input: { command: "cat > api/src/b.ts <<'EOF'\nexport {};\nEOF" } }),
      ['api/src/b.ts'],
    );
    assert.deepEqual(
      touchedAfter({ tool_name: 'Bash', cwd: project, tool_input: { command: 'echo "x = 1" >> services/summarizer/src/c.py' } }),
      ['services/summarizer/src/c.py'],
    );
  });
  test('tee targets', () => {
    assert.deepEqual(
      touchedAfter({ tool_name: 'Bash', cwd: project, tool_input: { command: 'printf "a" | tee -a api/src/a.ts api/src/b.ts >/dev/null' } }),
      ['api/src/a.ts', 'api/src/b.ts'],
    );
  });
  test('absolute paths resolve to project-relative entries', () => {
    assert.deepEqual(
      touchedAfter({ tool_name: 'Bash', cwd: '/tmp', tool_input: { command: `sed -i 's/a/b/' ${project}/api/src/a.ts` } }),
      ['api/src/a.ts'],
    );
  });
  test('read-only commands, /dev/null, descriptors and non-existent targets record nothing', () => {
    for (const command of [
      'git status',
      'cat api/src/a.ts',
      'npm test 2>&1 | tail -5',
      'echo hi > /dev/null',
      'echo hi > api/src/does-not-exist.ts; rm api/src/does-not-exist.ts',
      'grep -r "sed -i" docs/',
    ]) {
      assert.deepEqual(touchedAfter({ tool_name: 'Bash', cwd: project, tool_input: { command } }), [], command);
    }
  });
  test('a write outside the project records nothing', () => {
    const outside = tmpDir();
    fs.writeFileSync(path.join(outside, 'x.ts'), '');
    try {
      assert.deepEqual(
        touchedAfter({ tool_name: 'Bash', cwd: outside, tool_input: { command: 'echo 1 > x.ts' } }),
        [],
      );
    } finally {
      rmrf(outside);
    }
  });
});
