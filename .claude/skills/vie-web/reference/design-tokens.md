# Design tokens reference (salvaged from the old design-system skill, verified against index.css)

Core tokens live in `apps/web/src/index.css`, which imports `apps/web/src/styles/` modules
(`animations.css`, `categories.css`, `flow-grid.css`, `dark-effects.css`, `transitions.css`,
`overdrive.css`, `landing.css`). The source of truth for the design language is
[.impeccable/DESIGN.md](../../../../.impeccable/DESIGN.md); this file is the quick lookup.

## Category accents

Apply `.category-<name>` on the container; children read `var(--category-accent)` and
`var(--category-accent-soft)`. Dark mode surfaces become translucent automatically.

| Category | Accent | Category | Accent |
|---|---|---|---|
| cooking | `#FF6B35` | coding | `#22D3EE` |
| travel | `#10B981` | reviews | `#F59E0B` |
| fitness | `#EF4444` | education | `#8B5CF6` |
| podcast | `#EC4899` | gaming | `#6366F1` |
| diy | `#D97706` | standard | `#6B7280` |

Output components never use these directly: they colour with `--vie-accent` only.

## Icons (lucide-react, named imports, Tailwind sizing, `shrink-0` in flex)

| Concept | Icon |
|---|---|
| Cooking / Coding / Fitness / Travel | `ChefHat` / `Code2` / `Dumbbell` / `MapPin` or `Compass` |
| Reviews / Education / Podcast / Gaming / DIY / Default | `Star` / `Book` or `GraduationCap` / `Radio` / `Gamepad2` / `Wrench` or `Hammer` / `Film` |
| AI response / AI feature / thinking / suggestion | `Bot` / `Sparkles` / `Brain` / `Lightbulb` |
| Summary / key points / timestamps / code / recipe / cost / comparison | `FileText` / `ListChecks` / `Clock` / `Code2` / `UtensilsCrossed` / `DollarSign` / `GitCompare` |
| Achievement / goal / tip / warning / info | `Trophy` or `Award` / `Target` / `Lightbulb` / `AlertTriangle` / `Info` |
| Ratings | `Star` with `fill-current`, `StarHalf`, `ThumbsUp`/`ThumbsDown` |
| Chrome | `Settings`, `Sun`/`Moon`, `Menu`, `PanelLeft`/`PanelLeftClose`, `GripVertical`, `HelpCircle`, `LogOut` |

Copy feedback swaps `Copy` for `Check` in `text-emerald-500` (the one allowed non-semantic colour).
Sidebar icons size through `useSidebarTextClasses`.

## Premium utilities (`@layer components` in index.css)

Surfaces `block-card`, `block-accent`, `block-code-container`, `block-inline`; motion
`stagger-children` (lists with more than 3 items), `hover-lift`, `hover-scale`, `block-entrance`;
effects `glass-surface`, `text-gradient-primary`, `text-gradient-warm`, `fade-divider`,
`glow-success`; dark-mode glows `badge-glow-*`, `timer-glow`, `avatar-glow`.

## Scales

Card padding `p-5`, inner gap `space-y-3`, list gap `space-y-1.5`, block header
`text-xs font-semibold uppercase tracking-widest`, body `text-sm leading-relaxed`. Spacing is the
4px scale; radius `rounded-lg` base (12px), `rounded-xl` for block cards; borders use
`var(--border-width)`.
