# pipeline-1min run tools

Coordinator run wrappers, recovered on 2026-10-13 from the session transcripts after a WSL crash wiped
`/tmp`, and moved here so the next crash can't take them. Paths resolve from each script's own location.
Outputs go to `tools/out/<subdir>/`, which is gitignored, so they stay on disk but are never committed.
Credentials come from the repo `.env` at run time and are never written into these files.

Setup, from the repo root:

```bash
T=dev/active/pipeline-1min/tools; L="${VIE_HEAVY_LOCK:-/tmp/vie-heavy.lock}"
mkdir -p $T/out/{runs,golden,replay,capture,prod}
```

| Tool | What it does | Usage (repo root) | Calls | Spend | Output |
|---|---|---|---|---|---|
| `devrun.py` | One dev pipeline run as the eval user. Records the SSE timeline from the client side, plus `pipeline.timing` read from local Mongo. | `services/summarizer/.venv/bin/python $T/devrun.py <ytid> [--cold] [--no-bypass]`. When run on its own, first run the `.env` eval line from its docstring. | dev API (`DEVRUN_API`, default `localhost:3000`) and local Mongo (`MONGODB_URI`) | ≈ $0.15–0.22 per cold run | appends to `out/runs/devruns.jsonl`; summary on stdout |
| `devruns.sh` | Four cold dev runs, one at a time: T1dQhQAm8Tc, jMq8lEu-of0, uC45_4nnEAI, EqaMrzd9nZU. Was `devruns-p3.sh`. | `flock "$L" $T/devruns.sh <tag> > $T/out/runs/runs-<tag>.log 2>&1` | `devrun.py` ×4 (dev API). Reads `EVAL_USER_*` and `MONGODB_URI` from `.env`. | ≈ $0.63–0.70 per batch (g3) | `out/runs/<tag>-<ytid>.{json,err}` and `out/runs/devruns.jsonl` |
| `golden-full.sh` | Full golden eval: 18 live videos, concurrency 3, bypassCache. Then runs `gate.py` against `noise.json`. Run it alone. | `flock "$L" $T/golden-full.sh > $T/out/golden/golden-full.log 2>&1` | eval (`scripts/run_eval.py` → dev API) and `scripts/gate.py`. Reads `EVAL_USER_*` and `LANGFUSE_{PUBLIC,SECRET}_KEY`/`LANGFUSE_BASE_URL` from `.env`. | ≈ $1.74 per full run (g3, plan cache warm) | report goes to `reports/eval-*.json` (run_eval's own location); gate result on stdout |
| `replay3.sh` | Replays the 3 cassettes at `--speed 0.1` and prints the phase-timing tables. | `flock "$L" $T/replay3.sh > $T/out/replay/replay3.txt 2>&1` | replay (`scripts/replay.py`, fake LLM, no network) | $0 | stdout |
| `golden_table.py` | Builds a per-video markdown table of eval metrics against the per-video `noise.json` baseline, plus the caption track used. | `python3 $T/golden_table.py dev/golden-dataset/noise.json reports/eval-<stamp>.json [...]` | local dev Mongo, read-only, through `docker exec vie-mongodb mongosh` | $0 | stdout (markdown) |
| `capture.py` | Records the byte-exact LLM request baseline of the replay cassettes, using the in-process replay driver at speed 0. | `flock "$L" services/summarizer/.venv/bin/python $T/capture.py --out "$PWD/$T/out/capture/<label>" [--parallel-false] [--videos <id> ...] [--speed <x>]`. `--out` must be **absolute** because the script chdirs into `services/summarizer`. | replay, in-process (no network) | $0 | `<out>/<ytid>[.parallel-false].{jsonl,sha256}` |
| `prodrun.py` | One cold prod admin run. Records the client-side SSE timeline, then reads `pipeline.timing` and the 429 log counts from prod over SSH (read-only). Exits 4 on a refusal such as the daily cost cap. | invoked by `prodruns.sh` as `prodrun.py <ytid> <label>` | prod API (`VIE_PROD_API_URL`) and prod Mongo/logs over SSH (`VIE_PROD_SSH`, `VIE_PROD_SSH_KEY`) | ≈ $0.10–0.18 per run (g3/g4; plan ≈ $0.19 standard / $0.33 HIGH) | appends to `out/prod/prodruns.jsonl`; summary on stdout |
| `prodruns.sh` | Prod benchmark: admin, cold, one run at a time. Waits until no visitor run is active (polls every 30 s, up to 30 min). Stops at the first refusal. | `$T/prodruns.sh <ytid>:<label> ... > $T/out/prod/bench.log 2>&1` (no heavy lock: it runs on prod) | `prodrun.py` ×N. Reads `PROD_ADMIN_EMAIL`/`PROD_ADMIN_PASSWORD`, `VIE_PROD_API_URL`, `VIE_PROD_SSH` and `VIE_PROD_SSH_KEY` from `.env`. | ≈ $0.97 for the 7-run g3 benchmark | `out/prod/prod-<ytid>-<label>.{json,err}` and `out/prod/prodruns.jsonl` |
| `rebuild-images.sh` | Rebuilds dev images one at a time, then force-recreates their containers. Added separately; see its header. | `$T/rebuild-images.sh [service ...]` (takes the heavy lock itself) | docker compose (dev) | $0 | `out/rebuild/<UTC stamp>.log` |

Notes:
- **Heavy lock.** Apart from `rebuild-images.sh`, these tools never lock themselves. Wrap the local heavy ones (`devruns.sh`, `golden-full.sh`, `replay3.sh`, `capture.py`) in `flock "$L"` as shown, so only one heavy job runs on the WSL box at a time.
- **New `.env` keys for the prod tools** (no defaults):
  - `VIE_PROD_API_URL`: the prod API base URL.
  - `VIE_PROD_SSH`: the SSH destination, as `user@host`.
  - `VIE_PROD_SSH_KEY`: an absolute path to the identity file.
- **Fallback for `devrun.py`.** Without `MONGODB_URI`, `devrun.py` fails after the run when it reads the timing. If the URI's host is a `vie-*` compose name, the script rewrites it to `localhost`.
