---
name: vie-web
description: Repo facts for the vie-web frontend (React 19, Vite 7, Tailwind v4, shadcn, React Query, Zustand) and the admin UI. Loads on its own when files under apps/web/ or services/admin/ui/ are touched. Use when building components, pages, output blocks, themes, tests or Playwright specs.
paths:
  - "apps/web/**"
  - "services/admin/ui/**"
---

# vie-web

React 19 + Vite 7 + TypeScript, Tailwind v4 (CSS-first, `@theme inline {}` in
`apps/web/src/index.css`, no `tailwind.config.js`), shadcn/ui new-york primitives in
`src/components/ui/`, lucide-react icons, React Router 7, React Query 5 for server state, Zustand 5
for client state, `@xyflow/react` for canvases. Conventions: [.claude/rules/typescript.md](../../rules/typescript.md).
`services/admin/ui` is a separate npm project (not in the pnpm workspace) with the same stack
minus the vie library.

## Verify

```
pnpm --filter web exec tsc -b          # tsc --noEmit is hollow here (solution config); always -b
pnpm --filter web lint && pnpm --filter web exec vitest run
pnpm --filter web test:e2e:smoke       # Playwright on :5273; needs vie-api up (see CLAUDE.md)
cd services/admin/ui && npx tsc -b && npx vitest run
```

Sixteen stale specs live in the `legacy-stale` Playwright project (`E2E_LEGACY=1`); they already
fail and are not your regression. Vitest fake timers need `clearTimeout` and
`cancelAnimationFrame` stubbed or tests hang.

## Facts that bite

- The `vie-web` container serves a baked build with `VITE_*` compiled in; use `pnpm dev:web` for a
  live dev server. The e2e smoke project starts its own Vite on 5273, not the nginx build.
- Design language: read [.impeccable/DESIGN.md](../../../.impeccable/DESIGN.md) (tokens, spacing, motion) and
  [.impeccable/PRODUCT.md](../../../.impeccable/PRODUCT.md) (users, brand: sharp, generous, confident; dark mode is primary;
  violet-indigo identity, at most 4 accents per screen) before new UI. Design passes go through
  `/impeccable <command>` (shape, critique, audit, polish); [reference/design-tokens.md](reference/design-tokens.md) in this skill holds the
  category accent and icon tables.
- Output blocks compose the props-first, domain-free library in `src/components/vie/**` (barrel
  `@/components/vie`; `cards/`, `content/`, `data/`, `interactive/`, `media/`, `navigation/`,
  `feedback/`, `effects/`, `canvas/`). New components register in `components/vie/index.ts` and the
  output `component-registry`. They colour with `--vie-accent` only, never primary/cta
  (`scripts/check-accent-only.sh` fails CI otherwise).
- Semantic tokens only (`text-muted-foreground`, `bg-primary`); hex and palette classes break dark
  mode. New tokens: `:root` OKLCH value, `.dark` variant, `@theme inline` registration. Category
  theming is `.category-<name>` on the container exposing `--category-accent` and
  `--category-accent-soft`. Adding a theme follows the Lagoon theme pattern (memory note).
- `cn()` from `@/lib/utils` for class merging; CVA when a component has two or more variants;
  `shrink-0` on icons in flex rows; `aria-label` on icon-only buttons; no raw `<button>` outside
  `components/ui` (ESLint warns).
- React Flow canvases with floating edges need hidden handles or React Flow throws error #008.
- Watch-flow SSE: the `phase` values the UI accepts are `VALID_SSE_PHASES` + `SSE_PHASE_MAP`; a new
  backend phase without both entries freezes the progress UI.
- RTL output (Hebrew/Arabic) uses the Tailwind v4 `rtl:` variant; test both directions on text-heavy
  blocks. The Hebrew source-language toggle has its own fix history (memory note).
- Forms: Zod schema + controlled state via `src/lib/validation.ts` (`fieldErrorsFrom`);
  react-hook-form is not installed.
- Enter modes (Cooking/Build/Practice) key on `step_player`; assembly ships 8+ steps as
  `step_flow_canvas`.

## Read before changing

[docs/FRONTEND.md](../../../docs/FRONTEND.md) (structure, state, patterns), [.impeccable/DESIGN.md](../../../.impeccable/DESIGN.md), [docs/API-REFERENCE.md](../../../docs/API-REFERENCE.md)
for payload shapes, `packages/types/src/vie-response.ts` for the output contract.
