---
paths:
  - "api/**/*.ts"
  - "apps/web/**/*.ts"
  - "apps/web/**/*.tsx"
  - "services/admin/ui/**/*.ts"
  - "services/admin/ui/**/*.tsx"
  - "packages/types/**/*.ts"
  - "packages/shared/**/*.ts"
---

# TypeScript conventions (judgment calls only)

Already enforced by tooling, so do not restate them in reviews: `no-explicit-any`, `no-empty`,
react-hooks rules, `verbatimModuleSyntax` (forces `import type`), the raw `<button>` warning in
`apps/web`, strict `tsc`.

## Everywhere

- Exported and public functions declare their return type: `async create(...): Promise<Order>`.
- Imports come in three groups: external packages, internal modules, then type-only imports.
- `interface` for object shapes, `type` for unions and aliases.
- Destructure option objects in the body, not in the parameter list:
  `const { folderId, bypassCache = false } = options`.
- Template literals over string concatenation.
- Wrap the major I/O block in one try/catch, not every line. Check specific error types
  (`error instanceof Error && 'code' in error`). A non-blocking failure logs a warning and
  continues: `.catch(err => { this.logger.warn(...) })`.

## api/ and packages/

- Service methods are `async` class methods, not arrows; injected dependencies are
  `private readonly` constructor parameters.
- Routes attach Zod schemas, delegate to a service and map the reply. Services throw domain errors
  (`InvalidYouTubeUrlError`, `NotFoundError`) and never see HTTP. Repositories map documents with
  `toEntity()` and return domain objects, never raw `_id` documents.
- Utility functions are named declarations (`function validateUrl(url: string): boolean`);
  callbacks and transforms are arrows.

## apps/web/ and services/admin/ui/

- Components are `function ComponentName()`, never `const Component = () =>`. Memoised ones are
  `export const Sidebar = memo(function Sidebar() { ... })`.
- The props interface sits above the component and is named `ComponentNameProps`.
- Internal handlers are `handle*`; callback props are `on*`.
- One typed `useState` per value (`useState<StreamPhase>("idle")`); derive values during render
  instead of storing computed state.
- Conditional rendering: ternary for simple cases, `&&` for optional pieces, early return for guards.
- Server state lives in React Query, global client state in Zustand. Forms are Zod schemas over
  controlled state (`apps/web/src/lib/validation.ts`); react-hook-form is not installed.
