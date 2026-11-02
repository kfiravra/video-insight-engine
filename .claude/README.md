# `.claude2/` — replacement Claude Code infrastructure (built 2026-10-29)

A second Claude Code setup for this repo, built from the 2026-10-29 audit
(`dev/research/claude-infra-audit-2026-10-29.md`) so it can be compared with the current `.claude/`
and swapped in or out. It is inert until activated: Claude Code only reads `.claude/`.

Design in one line: instructions carry only what the code cannot tell you; every rule that cannot
bend is a hook, an `ask`/`deny` rule or a setting; skills trigger natively; verification is silent
on success and loud on failure; no dependencies.

## Inventory

| Path | Purpose | Loaded when |
|---|---|---|
| `CLAUDE.md` | verify commands, dev-loop gotchas, git policy, doc pointers (≤ 100 lines) | every session, as [.claude/CLAUDE.md](CLAUDE.md) |
| `settings.json` | hooks, `deny`/`ask` rules, `attribution: false`, subagent model default | every session |
| `mcp.json` | context7, playwright, mongodb (read-only, `directConnection=true`) | copied to `.mcp.json` by `activate.sh` |
| `rules/typescript.md`, `rules/python.md` | judgment conventions, path-scoped | when a matching file is read or edited |
| `skills/vie-api`, `vie-web`, `vie-python` | repo facts per area, `paths:` triggered | when a matching file is touched, or on demand |
| `skills/task-plan`, `resume`, `handoff` | task lifecycle on `dev/active/<task>/plan.md` + `notes.md` | user-invoked |
| `skills/check` | repo typecheck/lint/test runner for touched packages | model or user |
| `skills/phase-review`, `ship` | by-area review on two axes; pre-PR gate | user-invoked |
| `skills/diagnose`, `tdd` | feedback-loop debugging; red/green loop | model or user |
| `skills/guard` | `/guard off` stands the git guard down (writes `state/guard-off`), `/guard on` restores it; SessionStart prints `git guard: OFF` while it is off | user-invoked |
| `agents/reviewer.md`, `security-auditor.md` | read-only reviewers on opus | spawned by skills or on demand |
| `hooks/git-guard.sh` | PreToolUse Bash, checked per command segment: blocks stash, reset --hard/--merge/--keep, checkout --/./<path>/-f, switch --discard-changes, clean, restore, force-push, deleting main, worktree remove -f, update-ref -d; asks for push, rebase, PRs, container recreates; blocks `git add -A` during a swap; scans `bash -c "..."`; ignores heredoc prose; fails closed without jq | every Bash call |
| `hooks/guard-ctl.sh` | backs `/guard`: writes or removes `state/guard-off` | on demand |
| `hooks/post-edit.sh` | records touched files (Edit/Write path, or files a Bash command wrote via `sed -i`, `>`/`>>`, `tee`); `eslint --fix` / `ruff format` | every Edit/Write and every Bash call |
| `hooks/stop-check.sh` | tsc / eslint / ruff on touched packages (falls back to the dirty tree, once per tree state, when nothing was recorded); keeps file:line for every tool; blocks the stop once on real errors | every Stop |
| `hooks/session-start.sh` | branch, dirty count, active tasks, state GC | startup, resume, clear, compact |
| `hooks/lib/pkgmap.sh` | the one map from paths to check commands (hook and `/check` share it) | called by the above |
| `hooks/tests/*.test.mjs` | 195 tests, `node --test`, zero dependencies, temp state root; incident-named suites (2026-05-28 stash, 2026-05-31 TS6133, 2026-09-02 heredoc, 2026-10-29 Fable agents, 2026-11-01 guard gaps) | `node --test .claude/hooks/tests/*.test.mjs` |
| `standards/CODING-STANDARDS.md` | judgment rules for reviewers | read by `/phase-review` and `/ship` only |
| `standards/linter-followups.md` | eslint/ruff snippets to adopt later | never loaded |
| `activate.sh` | swap in / swap back | by you, from a plain shell |
| `state/` (gitignored) | per-session touched lists, error and tree hashes, `red` marker, `git-override`, `guard-off` | runtime |

## Activate, compare, revert

```
.claude2/activate.sh --dry-run     # prints the plan, changes nothing
.claude2/activate.sh               # .claude -> .claude1, .claude2 -> .claude, mcp.json -> .mcp.json
# restart Claude Code; check /status, /context, /mcp, /doctor, /skill-doctor
.claude/activate.sh --revert       # back to the old setup, byte for byte
```

The script refuses to run inside a Claude Code session (`$CLAUDECODE` set) unless `--force`,
because settings and hooks hot-reload mid-swap. It also refuses while `.claude/worktrees/` is
non-empty.

What a swap changes besides the two folders:

- The root `CLAUDE.md` is gone (deleted 2026-11-02 with the adoption commit), so only [.claude/CLAUDE.md](CLAUDE.md)
  loads. `/context` should list only it under Memory files.
- `.mcp.json` (gitignored) is backed up to `.claude1/mcp.root.json` and replaced by `mcp.json`.
- `.claude1/` is added to `.git/info/exclude` inside a marked block, removed on revert.
- `git status` shows the 126 tracked `.claude/**` files as deleted/modified while swapped. That is
  expected. The git guard refuses `git add -A`, `git add .`, `git add -u` and `git commit -a` while
  `.claude1/` exists so the swap is never committed by accident; add explicit paths.
- Sibling worktrees (`../vie-*`) keep the old tracked `.claude/`.

Adopted 2026-11-02 on `chore/claude-infra`: root `CLAUDE.md` and the `claudeMdExcludes` line are gone and
`.claude/` is committed. Still local: `.claude1/` and its exclude block, kept until the PR merges.

## Tests and maintenance

- `node --test .claude/hooks/tests/*.test.mjs` (or `.claude2/...` before the swap). Also run by the
  Stop hook whenever a hook file was edited, and by `/ship`.
- `/doctor prompt-audit` for stale or conflicting instructions, `/skill-doctor` for unused skills,
  `/checkup` for the setup as a whole, `claude plugin validate .claude/skills` for frontmatter.
- `VIE_STOP_PYRIGHT=1` adds pyright to the Stop check; `VIE_STOP_DEADLINE` (seconds, default 240)
  caps it.

## What was dropped from the old setup, and why

| Old piece | Usage in 60 days / 69 sessions | Replaced by |
|---|---|---|
| keyword skill-activation hook, `skill-rules.json`, pre-edit guard, read tracker | fired on 47 sessions, 794 hook errors on edits | native `paths:` + descriptions |
| per-prompt CONTEXT WARNING and ACTIVE TASKS reminders | ~50 lines per session | 4 to 8 lines at SessionStart |
| continuous-learning, post-tool-use tracker, auto-save-context hooks | [dev/gotchas.md](../dev/gotchas.md) never written | auto-memory, `/handoff`, `post-edit.sh` touched list |
| tsc banner on every Stop | real errors in 3 sessions | silent `stop-check.sh`, blocks once |
| 5 always-on ALWAYS/NEVER rules + `rules/README.md` | loaded every prompt | `standards/CODING-STANDARDS.md` for reviewers, 2 path-scoped rules |
| backend-node, backend-python, react-vite, design-system (32k words) | read 88 times by the hook | 3 skills under 100 lines with the repo-specific paragraphs salvaged |
| impeccable, impeccable2, 17 design passes (69k words) | 0 reads, 0 invocations | [.impeccable/DESIGN.md](../.impeccable/DESIGN.md) pointer + global `frontend-design` plugin |
| `/complete-task`, `/test`, `/security-check`, `/list-tasks`, `/task-plan-update`, `/update-docs`, `/audit-infra`, `/review` | never typed (`/review` 5 times) | `/ship`, `/check`, `/handoff`, `/resume` listing, `/phase-review`, built-ins |
| api-tester, frontend-error-fixer, plan-auditor, refactor-planner, debug-investigator agents | 0, 0, 0, 0, 2 calls | `diagnose` skill; `reviewer` + `security-auditor` on opus |
| `MultiEdit` references, bare `Bash`/`Edit`/`Write` allows, duplicate local allows | dead or ignored in bypass/auto mode | `deny` + `ask` rules only |
| `Figma MCP`, `github`, `octocode` servers | 0, 3, 0 calls; github token invalid, `gh` works | removed (re-add below) |
| 131 MB `hooks/node_modules`, vitest, `npx tsx` hooks (0.45 s each) | per-call latency | bash + jq, `node --test` |

Removed MCP entries, verbatim, for `mcp.json` if you want them back:

```json
"Figma MCP": { "command": "npx", "args": ["-y", "figma-developer-mcp", "--stdio"], "env": { "FIGMA_API_KEY": "${FIGMA_API_KEY}" } },
"github": { "type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" } },
"octocode": { "type": "stdio", "command": "npx", "args": ["octocode-mcp@latest"] }
```

## Scorecard

| Area | Old `.claude/` | mattpocock/skills | `.claude2/` |
|---|---|---|---|
| Instructions | 97-line CLAUDE.md + 5 always-on rules | 6-word CLAUDE.md | ≤ 100-line CLAUDE.md, 0 unscoped rules, 2 path-scoped |
| Skills | 4 × 8k-word tutorials, keyword hook, 19 unused design skills | 27 process skills, no repo layer | 3 repo skills with `paths:` + 8 process skills |
| Hard rules | prose + 1 good hook + 8 obsolete hooks | pre-commit, dependency-cruiser | 4 hooks (5 registrations) + 28 deny/ask rules + 3 settings |
| Verification | tsc banner every stop | pull-based TDD skill | silent scoped stop-check that blocks once, `/check` |
| Agents | 6 on Fable, 4 never used | none | 2 on opus, opus default for unset calls |
| Task state | 3 files + snapshot JSON, 19k-token resume | GitHub issues | 2 files, resume ≤ 6k tokens, legacy readable |
| Noise per session | ~90 reminder lines | 0 | ≤ 8 lines at SessionStart |
| Dependencies | 131 MB node_modules | none | none |
| Tests | 104, 2 red | none visible | 195 green |

## Changelog

- 2026-11-02: comparison fix list §6 items 1–5 (`dev/research/claude-infra-comparison-2026-11-01.md`).
  git-guard checks each `;`/`&&`/`||`/`|` segment on its own, scans quoted text when an interpreter runs it,
  ignores heredoc prose, knows `checkout <path>`, `switch --discard-changes`, `reset --merge|--keep`,
  `branch -d main`, `push +ref`, `worktree remove -f`, `update-ref -d`, and fails closed without jq.
  post-edit records files written through Bash; stop-check falls back to the dirty tree, keeps eslint and
  ruff file paths, drops deleted files and stops re-checking a red session. activate.sh decides the
  `settings.local.json` copy before the renames and reverts the first `mv` if the second fails. Tests use a
  temp state root. New: `/guard off|on`, `hooks/guard-ctl.sh`, `guard-off` state, pkgmap refuses a web
  typecheck without `tsconfig.app.json`. CLAUDE.md: dev/active never staged, no docs-only commits,
  push/PR/rebase on Kfir's word, spend stated before any run, Fable/Opus/Sonnet roles. 99 → 195 tests. Argument hints use the autocomplete convention (`[task-name]`, never `<task-name>`); `/guard` allowed-tools dropped the `.claude2` path.
- 2026-10-29: built from the audit (`dev/research/claude-infra-audit-2026-10-29.md`).

## Known limits

- `CLAUDE_CODE_SUBAGENT_MODEL=opus` is a default: it covers Agent calls that set no model. Explicit
  `sonnet` still works; built-in Explore runs on opus anyway; Plan and `fork` inherit the main
  model, so `/task-plan` passes `model: opus` explicitly.
- Skill names avoid the bundled `/debug`, `/verify` and `/review`; `resume` deliberately shadows the
  built-in session picker, as the old command did (`claude -r` still works).
- `dev/active/observability-fix` is a stale legacy task folder; `/resume` lists it until archived.
- The mongodb MCP assumes the dev compose (no auth). Set `MDB_MCP_CONNECTION_STRING` for anything else.
