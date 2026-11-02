# Coding standards (read by reviewers, not loaded every session)

`/phase-review` and `/ship` hand this file to the reviewer. It holds the judgment calls a linter
cannot make. Anything a tool already enforces is listed first so reviewers skip it.

## Enforced by tooling, skip in review

- TypeScript: `no-explicit-any`, `no-empty`, `no-unused-vars` (warn), react-hooks, react-refresh,
  `verbatimModuleSyntax`, strict `tsc`, raw `<button>` warning in `apps/web`. `console.log` in
  `api/src` is caught by `scripts/precommit-no-debug.sh`.
- Python: ruff `E`, `F`, `I` with the root `ruff.toml`; pyright basic in summarizer and assistant;
  `print(` in service `src/` is caught by `scripts/precommit-python.sh`.
- Design: `scripts/check-accent-only.sh` (CI) enforces accent-only colours in output components.
- Git: the PreToolUse hook and `ask` rules enforce the working-tree policy.
- Per-language conventions live in [.claude/rules/typescript.md](../rules/typescript.md) and [.claude/rules/python.md](../rules/python.md);
  reviewers read the one matching the diff.

## Size and structure

- Files stay under about 500 lines, functions under about 50 with few mutable locals. Beyond that,
  split by responsibility; a function with three or more phases becomes a pipeline of named steps
  with explicit inputs and outputs.
- One reason to change per module. Routes handle HTTP, services hold business logic, repositories
  hold data access; layers never skip.
- Duplicate once is fine; the third copy gets extracted. The wrong abstraction costs more than
  duplication.
- No dead code lands: unused imports, commented-out blocks, TODOs without a tracking reference,
  debug prints.
- Names say what a thing is: camelCase for TS values, snake_case for Python, PascalCase for
  types/classes/components, SCREAMING_SNAKE_CASE for constants.
- Comments explain why, not what. If a comment explains what the code does, extract a named
  function instead.

## Errors

- Validate every external input at the boundary (Zod on routes, Pydantic on FastAPI), then trust it
  inside.
- Throw typed domain errors from services; map to HTTP in one error handler; never expose stack
  traces, internal paths or raw driver errors to clients.
- No empty catch blocks and no returning `null` for failures. Non-blocking failures log a warning
  with context and continue.
- Independent async operations run concurrently (`Promise.all`, `asyncio.gather`).

## Tests

- New behaviour ships with tests; bug fixes ship with a regression test that fails before the fix.
- Test behaviour through public interfaces, not implementation. Names read as
  "should <behaviour> when <condition>".
- Mock only external dependencies (LLM calls, Qdrant, network); never the thing under test. Clean
  mocks up after each test.
- `QdrantClient` calls are sync: mock with `MagicMock`, not `AsyncMock`.

## Security

- No secrets in code, logs or test fixtures; env vars only. Never log tokens, keys or PII.
- Auth is checked on every protected route and ownership is checked at the data layer.
- CORS comes from config (`FRONTEND_URL`, `CORS_ADDITIONAL_ORIGINS`), never `*`.
- Rate limits apply to auth endpoints and expensive operations ([docs/SECURITY.md](../../docs/SECURITY.md)).
- GDPR deletions go through the cascade ([docs/GDPR.md](../../docs/GDPR.md)); never delete user data ad hoc.

## Git

- Conventional commits: `type(scope): description`, one logical change per commit, one green phase
  per commit. No attribution trailers (a setting disables them).
- Feature branches only; `main` is protected. Push, PR and history rewrites need an explicit verb
  from the user in the current turn.
