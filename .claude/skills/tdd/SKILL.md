---
name: tdd
description: Test-driven development for this repo. Use when the user wants to build a feature or fix a bug test-first, mentions red-green-refactor, or asks for integration tests.
---

Adapted from mattpocock/skills `tdd` (MIT, a496f55).

## Seams first

A seam is the public boundary you test at. Before writing any test, list the seams under test with
one line each on what it catches and what it misses, and confirm them with the user. In this repo
the usual seams are: a Fastify route via `app.inject` (api), a service method with injected fakes
(api), a pipeline stage function with a fixture transcript (summarizer), a FastAPI route with the
test client (assistant, admin), a component with Testing Library (web), a Playwright smoke flow.

## The loop

- Red before green: one failing test, then only enough code to pass it.
- One vertical slice per cycle (one seam, one test, one implementation), each a tracer bullet that
  reacts to what the last cycle taught you. Never all tests first.
- Refactoring is not part of the loop; it belongs to `/phase-review`.
- Run the suite through `/check --tests` for the touched package only; never the three suites in
  parallel.

## While a test is deliberately red

`touch .claude/state/red` so the Stop hook reports type errors instead of blocking on them, and
`rm -f .claude/state/red` as soon as the slice is green. Do not leave the marker behind.

## What a good test is

It verifies behaviour through the public interface, reads like a specification ("user can submit a
cached video and gets the stored response"), and survives refactors. Expected values come from an
independent source (a fixture, the spec, a known-good literal), never recomputed the way the code
computes them.

Anti-patterns to refuse: mocking internal collaborators or asserting through a side channel
(querying Mongo instead of using the interface); tautological assertions; testing private
functions; snapshot tests of large LLM outputs. Mock only external dependencies (LiteLLM, Qdrant,
S3, YouTube). `QdrantClient` is sync: `MagicMock`, not `AsyncMock`.
