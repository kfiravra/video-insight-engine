# Linter follow-ups (not applied; repo config changes for later)

The old `.claude/rules/code-quality.md` stated these as prose. They are mechanical, so they belong in
the linters. Apply when convenient; each block is independent. Expect existing violations the first
time, so start with `warn` or fix in a dedicated commit.

## ESLint (`api/eslint.config.js`, `apps/web/eslint.config.js`, `services/admin/ui/eslint.config.js`)

Add to the main rules object:

```js
rules: {
  'max-lines': ['warn', { max: 500, skipBlankLines: true, skipComments: true }],
  'max-lines-per-function': ['warn', { max: 50, skipBlankLines: true, skipComments: true, IIFEs: true }],
  'no-console': ['error', { allow: ['warn', 'error'] }],
  '@typescript-eslint/consistent-type-definitions': ['error', 'interface'],
},
```

Keep the existing test-file override and add `'max-lines-per-function': 'off'` to it (describe
blocks are long by design). With `no-console` in place, `scripts/precommit-no-debug.sh` becomes
redundant for `api/src`.

## Ruff (`ruff.toml` at the root; `services/summarizer/pyproject.toml` mirrors it)

```toml
[lint]
select = ["E", "F", "I", "B", "UP", "T201", "S110", "C901", "PLR0915"]

[lint.mccabe]
max-complexity = 12

[lint.pylint]
max-statements = 50
```

- `T201` replaces the `print(` grep in `scripts/precommit-python.sh`; keep the existing
  per-file-ignores and add `"**/tests/**" = ["T201", "S110"]`, `"**/scripts/**" = ["T201"]`.
- `UP` with `target-version = "py311"` keeps `X | None` and f-strings mechanical.
- `S110` flags `try/except/pass`.

## Pre-commit note

`scripts/precommit-python.sh` runs `ruff check --fix`, which deletes `import x as x` re-exports and
imports that are not used yet. If that bites again, switch it to `ruff check` (no `--fix`) and let
the PostToolUse hook keep `ruff format` only, which is what `.claude/hooks/post-edit.sh` does.
