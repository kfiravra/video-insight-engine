# Common Gotchas

Project-wide bug knowledge base. **Check here BEFORE debugging.**

This file persists across sessions to capture learnings from past bugs.
When you fix a bug, add it here to prevent re-investigation.

---

## How to Use

1. **Before debugging**: Search this file for similar symptoms
2. **After fixing**: Add new gotcha with symptom, cause, and fix
3. **Review periodically**: Remove outdated entries

---

## vie-api (Node.js/Fastify)

| Symptom | Cause | Fix | Added |
|---------|-------|-----|-------|
| 401 on protected routes | Missing/expired JWT | Check token expiry, refresh flow | 2026-02 |
| 500 on MongoDB operations | Connection timeout | Check MONGODB_URI, increase timeout | 2026-02 |
| Route not found (404) | Registration order | Register routes after plugins | 2026-02 |
| SSE not streaming | Missing flush/proper headers | Use `reply.raw.write()` + flush | 2026-02 |
| Validation errors on good input | Zod schema mismatch | Check schema vs actual payload | 2026-02 |

---

## vie-web (React/Vite)

| Symptom | Cause | Fix | Added |
|---------|-------|-----|-------|
| Component not re-rendering | Missing dependency in useEffect | Add all deps or use useCallback | 2026-02 |
| Stale data in React Query | Cache not invalidated | Call `queryClient.invalidateQueries()` | 2026-02 |
| CORS errors in dev | Vite proxy misconfigured | Check `vite.config.ts` proxy | 2026-02 |
| Hydration mismatch | Server/client render difference | Check conditional rendering | 2026-02 |
| CSS not applying (Tailwind) | Class not in safelist | Check content paths in tailwind config | 2026-02 |

---

## vie-summarizer (Python/FastAPI)

| Symptom | Cause | Fix | Added |
|---------|-------|-----|-------|
| LLM timeout | Long transcript + slow model | Increase timeout, use faster model | 2026-02 |
| Rate limit errors | Provider quota exceeded | Switch provider or add retry | 2026-02 |
| Transcript empty | Video unavailable/private | Check video accessibility | 2026-02 |
| JSON parse error in response | LLM output malformed | Add response validation, retry | 2026-02 |
| Memory spike | Large transcript in memory | Stream processing, chunk | 2026-02 |
| yt-dlp 403 on video download / stream-URL seeks | YouTube 403s the **web client's** download URLs from some environments (client-bound googlevideo CDN) | Set `YTDLP_PLAYER_CLIENTS=android` (the default). **Never** use `android,default` — the merged format list lets bestvideo pick a blocked web DASH format. android caps at 640×360 (PO-token wall); the hires pass falls back to a local ≤720p download. Low-res cached runs self-heal via the manifest `hiresCount==0` gate | 2026-08 |
| Frames silently stale after quality changes | `scenes-v3` keys overwrite in place — old docs get new frames but stale metadata | Bump `SCENE_S3_PREFIX` on frame-quality changes, or `?bypassCache=true` per video | 2026-08 |
| `test_stream_routes` error tests get a `cached` event instead of `error` (5 failures, only on this box) | Tests stub the event broker but not `response_cache`, which connects to the **live dev Redis**; it holds `vie:response:v8:dQw4w9WgXcQ` (the fixture's youtubeId) from a real run | `_reset_response_cache` now monkeypatches `get_response`/`set_response`. Any new test that reaches the producer path must stub the response cache — never rely on Redis being empty | 2026-09 |

---

## Docker / Infrastructure

| Symptom | Cause | Fix | Added |
|---------|-------|-----|-------|
| Container can't connect to DB | Network misconfigured | Use service name, not localhost | 2026-02 |
| Hot reload not working | Volume not mounted | Check docker-compose volumes | 2026-02 |
| Port already in use | Dangling container | `docker-compose down` first | 2026-02 |
| Build fails (npm install) | Package-lock stale | Delete node_modules, reinstall | 2026-02 |
| Dev eval collapses across services at once: LLM calls time out (`asyncio.timeout` firing seconds late), `getaddrinfo EAI_AGAIN vie-mongodb`/`vie-summarizer`, pymongo "Temporary failure in name resolution", `docker logs` silent for many minutes then a flood (Docker Desktop on WSL2) | The WSL2 VM runs out of memory: swap 100 % used, kernel `page allocation failure: order:7` in `hvs_probe`/`vmbus_alloc_ring` (Hyper-V vsock). Docker Desktop's daemon then stalls (embedded DNS 127.0.0.11 and log collection) for every container, and the whole VM freezes for seconds at a time. Host IDE/LSP servers (tsserver, pyright, pylance) can hold 3-5 GB of the default 8 GB VM. Not a pipeline-code regression: the idle `vie-summarizer-worker` shows the same heartbeat log gap | Before a multi-video eval, check `free -m` and `dmesg \| grep "page allocation failure"`. Free memory (close idle LSP/IDE sessions, `wsl --shutdown`, restart Docker Desktop) or raise `memory=` in `%USERPROFILE%\.wslconfig`. Run dev evals at concurrency 2 or less | 2026-10 |

---

## Cross-Service Issues

| Symptom | Cause | Fix | Added |
|---------|-------|-----|-------|
| API → Summarizer timeout | Long processing | Increase Fastify timeout | 2026-02 |
| Type mismatch between services | @vie/types out of sync | Rebuild shared packages | 2026-02 |
| SSE disconnects randomly | Proxy/load balancer | Disable buffering, long timeout | 2026-02 |

---

## Adding New Gotchas

When you fix a bug, add it using this format:

```markdown
| [Brief symptom] | [Root cause] | [How to fix] | [YYYY-MM] |
```

Guidelines:
- Keep symptom brief but searchable
- Cause should explain "why" not just "what"
- Fix should be actionable
- Date helps with relevance
