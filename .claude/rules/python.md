---
paths:
  - "services/**/*.py"
  - "packages/llm-common/**/*.py"
---

# Python conventions (judgment calls only)

Already enforced by tooling, so do not restate them in reviews: ruff `E`, `F`, `I` from the root
`ruff.toml` (with per-file ignores for summarizer and assistant), pyright basic in summarizer and
assistant, and the `print(` guard in `scripts/precommit-python.sh`. The target is Python 3.11 (CI);
local venvs run 3.12, so do not rely on 3.12-only syntax.

- Type hints on every signature, parameters and return: `async def classify(...) -> Result | None:`.
  Write `X | None`, never `Optional[X]`.
- `from __future__ import annotations` at the top of every module.
- f-strings for interpolation.
- `async def` for anything that does I/O, and never call sync I/O inside an async handler. The
  summarizer pipeline uses sync pymongo on purpose (it runs in a worker process); assistant and
  admin use Motor and `await` every call.
- Module-level logger: `logger = logging.getLogger(__name__)`. Structured context goes in the call
  (`logger.error({"youtubeId": ..., "error": ...}, "message")`), not in the message string.
- Private helpers start with `_`; helpers sit above the functions that use them; sections are
  separated with `# ─── Section ───` dividers.
- `dataclass` for value objects, `BaseModel` for validated input; `SCREAMING_SNAKE_CASE` constants;
  `frozenset` for immutable collections.
- Imports in four groups: `__future__`, stdlib, third-party, local. `TYPE_CHECKING` blocks break
  cycles; relative imports inside a service (`from .assemblers import ...`).
- Catch specific exceptions, never bare `except:`. A non-blocking failure logs a warning and returns
  `None`. LLM calls go through `call_llm_with_retry`.
- Services raise domain exceptions, never `HTTPException`; routes translate. Use `Depends()` for
  injection and `asyncio.gather()` for independent I/O.
- `@lru_cache(maxsize=1)` on deterministic file-loading helpers (prompts, config).
