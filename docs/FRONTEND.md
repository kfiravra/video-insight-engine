# Frontend Development Guide

Complete guide for vie-web frontend development: React, TypeScript, Tailwind v4, shadcn/ui, state management.

---

## Overview

### Tech Stack

| Technology      | Version | Purpose           |
| --------------- | ------- | ----------------- |
| Vite            | 7.x     | Build tool        |
| React           | 19.x    | UI framework      |
| React Compiler  | latest  | Auto-memoization  |
| TypeScript      | 5.x     | Language          |
| Tailwind CSS    | 4.x     | Styling           |
| shadcn/ui       | latest  | Component library |
| React Query     | 5.x     | Server state      |
| Zustand         | 5.x     | Client state      |
| React Router    | 7.x     | Routing           |

> Not installed (removed 2026-07 dead-deps cleanup): React Hook Form, Vercel AI SDK. Forms use controlled inputs + Zod; LLM streaming is hand-rolled SSE (`lib/streaming/`).

### Environment Variables

```bash
VITE_API_URL=http://localhost:3000/api
VITE_WS_URL=ws://localhost:3000/ws
```

---

## Project Structure

```
apps/web/src/
├── main.tsx
├── App.tsx
│
├── pages/                          # ALL route pages
│   ├── LoginPage.tsx
│   ├── RegisterPage.tsx
│   ├── LandingPage.tsx             # Public homepage with URL input
│   ├── BoardPage.tsx               # Folder explorer + video grid
│   ├── GeneratePage.tsx            # URL intake + first-run onboarding (value-prop chips, example disclosure, sample URL)
│   ├── VideoDetailPage.tsx         # Video detail + output rendering
│   ├── SharePage.tsx               # Public share view (/s/:slug)
│   └── dev/                        # DesignSystemPage, StreamingPreviewPage (/dev/streaming-preview, DEV-only)
│
├── components/                     # SHARED components
│   ├── ui/                         # shadcn/ui primitives + Lightbox (frame viewer, capture-phase Escape)
│   ├── vie/                        # VIE Component Library
│   │   ├── index.ts                # Barrel — import from '@/components/vie'
│   │   ├── cards/                  # GlassCard, ExpandableCard, HeroCard, VideoHero (+ domain-meta.ts)
│   │   ├── data/                   # ScoreRing, StatPill, Badge, Timer, Timestamp, VisualEvidence/EvidenceImage, CostDisplay, KeyValue
│   │   ├── content/                # TextBlock, CodeSnippet, QuoteBlock, TableView
│   │   ├── navigation/            # TabBar, ProgressBar, SectionNav, Stepper
│   │   ├── interactive/           # CheckItem, FlipCard, OptionGrid, ActionButton
│   │   ├── feedback/              # Celebration, FadeIn, InlineScore, Shake
│   │   └── media/                 # VideoClip, ImageGallery, AudioSnippet
│   ├── layout/                     # App shell
│   │   ├── Layout.tsx, AppHeader.tsx, LeftSidebarIconStrip.tsx
│   │   ├── MobileBottomNav.tsx, MobileFAB.tsx
│   ├── collections/                # Collection picker/panel (4 files)
│   ├── rag/                        # RAG chat components (4 files)
│   ├── videos/                     # VideoGrid, VideoCard, YouTubePlayer, etc.
│   ├── playlists/                  # PlaylistPreview
│   └── dev/                        # Design system showcases
│
├── features/                       # Complex, multi-concern features
│   ├── sidebar/                    # Navigation system (30+ files)
│   │   ├── Sidebar.tsx, index.ts   # Root + barrel (lazy import)
│   │   ├── core/                   # SidebarHeader, SidebarToolbar, SidebarTabs,
│   │   │                           # SidebarSection, SearchInput, SortDropdown,
│   │   │                           # TextSizeToggle, DndProvider, SelectionToolbar
│   │   ├── folders/                # FolderItem, FolderTree, FolderContextMenu,
│   │   │                           # FolderTreeSelect, FolderSelector, FolderRenameInput,
│   │   │                           # CreateFolderButton, CreateSubfolderInput, NewFolder*
│   │   ├── videos/                 # VideoItem, VideoContextMenu, UnassignedVideosList, AddVideoInput
│   │   ├── dialogs/                # BulkDeleteDialog, DeleteFolderDialog, DeleteVideoDialog
│   │   ├── hooks/                  # use-sidebar-text-size, use-long-press, use-is-truncated,
│   │   │                           # use-folder-drag-drop, use-sidebar-chat
│   │   └── lib/                    # folder-utils, style-utils, layout-constants
│   │
│   └── video-output/               # Video output rendering (40+ files)
│       ├── components/
│       │   ├── OutputRouter.tsx     # Routes v2 (assembledTabs) or v1 fallback; filmstrip-tab suppression
│       │   ├── StreamingPlaceholder.tsx  # StreamingPlaceholder + StreamErrorCard (pre-tab states)
│       │   └── output/
│       │       ├── ComposableOutput.tsx, ComposableOutputV1.tsx
│       │       ├── component-registry.tsx  # COMPONENT_REGISTRY (29 renderers)
│       │       ├── DisplaySection.tsx, TabLayout.tsx, CrossTabLink.tsx, TabIntro.tsx
│       │       ├── TabCoordinationContext.tsx  # Cross-tab state coordination
│       │       ├── FlowPlayer.tsx, flow-modes.tsx, RecipePlayer.tsx, RecipeStepView.tsx
│       │       ├── interactive/    # Interactive renderers (+ MomentGalleryCard,
│       │       │                   #   MomentTimelineRow, moment-utils)
│       │       └── lib/            # tab-data-resolver, format-utils, ingredient-step-matcher
│       ├── hooks/                  # use-summary-stream, use-processing-manager, use-focus-band
│       ├── stores/                 # processing-store
│       ├── contexts/               # TabStateContext, VideoPlayerContext
│       └── lib/                    # streaming/ (SSE pipeline incl. resolve-display-tabs),
│                                   #   synthesis-utils, output-type-config, tab-prop-schemas (Zod boundary)
│
├── contexts/                       # App-level React contexts
│   └── DirectionContext.tsx        # RTL/LTR direction + language provider
├── api/                            # API client modules
├── hooks/                          # SHARED hooks (8 files)
│   ├── use-folders, use-videos, use-playlists   # Data hooks
│   ├── use-share, use-websocket, use-theme      # Feature hooks
│   └── use-media-query, use-drag-scrollbar      # UI utility hooks
├── lib/                            # SHARED utilities (6 files)
│   ├── utils.ts (cn()), query-client.ts, query-keys.ts
│   ├── youtube-utils.ts, string-utils.ts
│   ├── i18n.ts                     # UI label translations (en, he, ar)
│   └── dev/                        # Dev mock data
├── stores/                         # GLOBAL stores (auth-store, ui-store)
├── styles/                         # Global CSS
├── test/                           # Test setup + mocks
└── types/                          # Shared types
```

---

## Routes

| Path             | Page            | Auth       | Description                         |
| ---------------- | --------------- | ---------- | ----------------------------------- |
| `/`              | LandingPage     | Public     | URL input; auto-redirects to /board if authenticated |
| `/login`         | LoginPage       | Public     | Sign in → redirects to /board       |
| `/register`      | RegisterPage    | Public     | Sign up → redirects to /board       |
| `/board`         | BoardPage       | Protected  | Folder explorer + video grid (home) |
| `/generate`      | GeneratePage    | Protected  | URL intake; onboarding affordances collapse for users with ≥1 video |
| `/video/:id`     | VideoDetailPage | Protected  | Video detail + output rendering     |
| `/s/:slug`       | SharePage       | Public     | Public read-only shared output      |

---

## Theme System

### Architecture

Theme uses `data-theme` attribute on `<html>` with four modes:

| Mode     | Behavior                                      |
| -------- | --------------------------------------------- |
| `dark`   | Forces dark theme (`data-theme="dark"`)       |
| `light`  | Forces light theme (`data-theme="light"`)     |
| `system` | No attribute set — CSS `prefers-color-scheme` controls |
| `lagoon` | Colorful dark-family variant (`data-theme="lagoon"`) — deep-teal primary + warm coral CTA |

`lagoon` is a **dark-family** theme: it matches the `@custom-variant dark`
selector and the `:is([data-theme="dark"])` rules, so it inherits dark-mode prose,
effects, and the mobile glass-blur media query. Adding another dark-family theme
means extending all of those selectors plus the `index.html` pre-paint script.
`ResolvedTheme` is `"dark" | "light" | "lagoon"` (`"system"` always resolves away).

### Key Files

- **`theme-context.ts`** — `Theme = "dark" | "light" | "system" | "lagoon"`, context + provider types
- **`theme-provider.tsx`** — Sets `document.documentElement.dataset.theme`, listens to `matchMedia` for system mode, uses View Transitions API for smooth crossfade
- **`index.html`** — FOUC prevention script reads `localStorage('vie-theme')` and sets `data-theme` before React loads

### Color Palette (OKLCH)

- **Primary**: VIE palette (replaces violet-indigo)
- **Accents**: 8 tokens — `--vie-coral`, `--vie-plum`, `--vie-sky`, `--vie-mint`, `--vie-honey`, `--vie-rose`, `--vie-forest`, `--vie-peach`
- **Per-output gradients**: Each output type has a unique gradient token
- **Light mode**: Cool blue-gray (hue ~250), low chroma
- **Dark mode**: Warm amber-brown (hue ~55), low chroma

### Fonts

- **Display**: Bricolage Grotesque (weight 800; H1 / page titles / marketing headlines)
- **Body**: Hanken Grotesk (H3 / H4 / body / UI controls) — `system-ui, -apple-system, "Segoe UI"` fallbacks
- **Mono**: JetBrains Mono (code, kbd keys, timestamps; calt disabled in code surfaces)
- **No-Inter Rule**: Inter / DM Sans / Plus Jakarta are banned in `apps/web/src/` — they signal the generic-AI-tool look. Use `.type-*` semantic utilities defined in `apps/web/src/index.css` (`.type-page-title`, `.type-eyebrow`, `.type-caption`, `.type-hero-xl`) for the full ramp.
- Loaded via `<link>` in `index.html` with `font-display: swap`

---

## Multi-Language & RTL Support

Video output components support right-to-left (RTL) languages. Direction is determined per-video based on the detected transcript language.

### Architecture

```
OutputRouter
  └── DirectionProvider (language, isRTL)      ← wraps all output
        ├── useDirection() → { language, isRTL, dir }
        └── useLabels() → translated UI strings
```

### Key Files

- **`contexts/DirectionContext.tsx`** — React context providing `language`, `isRTL`, `direction` ("ltr" | "rtl")
- **`lib/rtl.ts`** — `isRTL(language)` + `directionFor(language)` helpers; canonical RTL ISO 639-1 codes (he, ar, fa, ur, ps, sd, yi, ku, dv)
- **`lib/i18n.ts`** — UI label translations (English, Hebrew, Arabic); `useLabels()` hook
- **`OutputRouter.tsx`** — Wraps output tree in `<DirectionProvider language={language} isRTL={isRTLProp ?? isRTLLanguage(language)}>` (falls back to language inference when `meta.isRTL` is absent)
- **`index.css`** — Registers `@custom-variant rtl (&:where([dir="rtl"], [dir="rtl"] *))` so Tailwind v4 compiles `rtl:` utilities (without this, every `rtl:` class silently drops)

### RTL Patterns

| Pattern | LTR | RTL |
|---------|-----|-----|
| Text alignment | `text-left` | Use `text-start` (auto-flips) |
| Directional icons | Normal | `rtl:rotate-180` on chevrons/arrows |
| Margins/padding | `ml-2`, `mr-2` | Use `ms-2`, `me-2` (logical properties) |
| Flex direction | Default | Tailwind `rtl:` variant auto-flips |
| Timestamps / code blocks | Normal | `dir="ltr"` on the element (always LTR — numerals + monospace) |
| Mixed-script user content | Normal | `dir="auto"` (lyric lines, transcript snippets) |
| Keyboard / swipe direction | ArrowRight = forward | `useDirection().isRTL` mirrors: ArrowLeft = forward, swipe-left = forward |

### UI Labels

Components use `useLabels()` for translatable strings (Next, Previous, Done, etc.) instead of hardcoded English. Currently supports: English, Hebrew, Arabic.

### Source-Language Toggle (`LanguageToggle`)

For non-English videos the backend promotes English to primary and nests the original-language artifact under `video.sourceLanguage` (see [API-REFERENCE.md](./API-REFERENCE.md#get-videosid) and [DATA-MODELS.md](./DATA-MODELS.md#videoSummaryCache)). The FE renders a toggle so users can flip between the English-primary view and the original.

- **Component**: `apps/web/src/features/video-output/components/LanguageToggle.tsx` — WAI-ARIA radiogroup, arrow-key navigable, `lang` attribute applied per pill (assistive-tech announces the right language for each label).
- **Persistence**: `localStorage` key `vie:prefersOriginalLang` (cross-video, cross-session). A Hebrew speaker who toggles to the original once lands on the original on every subsequent video and every reload. Reading is guarded against private-mode `SecurityError` — falls back to English-primary.
- **Render gate**: only render the toggle when `video.sourceLanguage` is present AND `sourceLanguage.tabs.length > 0`. English-source and sound-only videos omit `sourceLanguage` entirely (not `null`).
- **Consumption pattern** (`apps/web/src/pages/VideoDetailPage.tsx`):
  ```tsx
  const sourceLanguage = video?.sourceLanguage ?? null;
  const canToggleLanguage = !!sourceLanguage && sourceLanguage.tabs.length > 0;

  // RTL streaming gate — see warning below.
  const useOriginal = !isProcessing && showOriginal && canToggleLanguage;

  const resolvedTabs = useOriginal ? sourceLanguage!.tabs : video.tabs;
  const resolvedMeta = useOriginal ? sourceLanguage!.meta : video.meta;
  ```

> **⚠️ RTL streaming gate (gotcha)**: `useOriginal` MUST be gated on `!isProcessing` (or `!isStreaming` — same idea). Streamed content during the pipeline is always English-shape; `sourceLanguage` is only populated after the final translation phase. Without the gate, a Hebrew user with `showOriginal=true` persisted would land on a streaming page that flips `dir="rtl"` on the wrapper while LTR-English stream content loads inside — full-page layout misalignment until the stream completes.

---

# Styling - Tailwind v4

## CSS-First Configuration

Tailwind v4 uses CSS for configuration, not `tailwind.config.ts`.

### Setup

```typescript
// vite.config.ts
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    react({
      babel: {
        plugins: [["babel-plugin-react-compiler"]],
      },
    }),
    tailwindcss(),
  ],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
});
```

### Theme Configuration

```css
/* src/index.css */
@import "tailwindcss";
@import "tw-animate-css";

@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));

@theme inline {
  /* Colors (OKLCH for better gradients) */
  --color-background: oklch(100% 0 0);
  --color-foreground: oklch(14.1% 0.005 285.82);
  --color-primary: oklch(20.5% 0.015 285.82);
  --color-primary-foreground: oklch(98.5% 0 0);

  /* Semantic */
  --color-destructive: oklch(57.7% 0.245 27.33);
  --color-success: oklch(59.6% 0.145 163.22);
  --color-warning: oklch(79.5% 0.184 86.05);

  /* Border & Input */
  --color-border: oklch(91.4% 0.004 285.82);
  --color-ring: oklch(20.5% 0.015 285.82);

  /* Radius */
  --radius: 0.625rem;
}

.dark {
  --color-background: oklch(14.1% 0.005 285.82);
  --color-foreground: oklch(98.5% 0 0);
  /* ... other dark overrides */
}

@layer base {
  * { @apply border-border; }
  body { @apply bg-background text-foreground; }
}
```

### Breaking Changes from v3

| v3 Name        | v4 Name          | Notes               |
| -------------- | ---------------- | ------------------- |
| `shadow-sm`    | `shadow-xs`      | Scale shifted down  |
| `shadow`       | `shadow-sm`      | Scale shifted down  |
| `rounded-sm`   | `rounded-xs`     | Scale shifted down  |
| `ring`         | `ring-3`         | Now defaults to 1px |
| `outline-none` | `outline-hidden` | Name clarification  |

### The `cn()` Utility

```typescript
// src/lib/utils.ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

Usage:

```tsx
<div className={cn(
  "rounded-lg border bg-card p-6",
  isActive && "ring-2 ring-primary",
  className
)} />
```

### Component Variants with CVA

```tsx
import { cva, type VariantProps } from "class-variance-authority";

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-md font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline: "border border-input bg-background hover:bg-accent",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        destructive: "bg-destructive text-white hover:bg-destructive/90",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        default: "h-9 px-4 text-sm",
        lg: "h-10 px-6 text-base",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);
```

### Responsive Design (Mobile-First)

```tsx
<div className="
  flex flex-col gap-4      /* Mobile: stack */
  md:flex-row md:gap-6     /* Tablet+: row */
  lg:gap-8                 /* Desktop: more space */
">
```

| Prefix | Min Width | Target           |
| ------ | --------- | ---------------- |
| (none) | 0px       | Mobile (default) |
| `sm:`  | 640px     | Large phones     |
| `md:`  | 768px     | Tablets          |
| `lg:`  | 1024px    | Laptops          |
| `xl:`  | 1280px    | Desktops         |

---

# Components - shadcn/ui

## Philosophy

shadcn/ui components are **copied into your project** - you own the code.

### Setup

```bash
pnpm dlx shadcn@latest init
# Style: New York, Base color: Zinc, CSS variables: Yes
```

### Add Components

```bash
pnpm dlx shadcn@latest add button card input label form dialog sonner badge skeleton dropdown-menu tabs scroll-area
```

### Core Components

**Button:**
```tsx
<Button>Default</Button>
<Button variant="secondary">Secondary</Button>
<Button variant="outline">Outline</Button>
<Button variant="ghost">Ghost</Button>
<Button variant="destructive">Delete</Button>
<Button size="sm">Small</Button>
<Button size="lg">Large</Button>
<Button variant="ghost" size="bare">Text-like action</Button>
<Button variant="ghost" size="icon-bare"><Copy className="h-3 w-3" /></Button>
<Button disabled>
  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
  Loading...
</Button>
```

**Button size guide:**
| Size | Use case | Styles |
|------|----------|--------|
| `default` | Standard buttons | `h-9 px-4 py-2` |
| `sm` | Compact buttons | `h-8 px-3` |
| `lg` | Large CTAs | `h-10 px-6` |
| `icon` | Icon-only (standard) | `size-9` |
| `bare` | Text-like inline actions (copy, seek, expand) | `h-auto p-0 gap-1` |
| `icon-bare` | Icon-only minimal (toggles, play/stop) | `h-auto p-0.5` |

> **ESLint:** Raw `<button>` elements are flagged by `no-restricted-syntax` (warn level). Always use `<Button>` from `@/components/ui/button`. Exceptions: Radix `asChild` composition requires raw elements — use `// eslint-disable-next-line no-restricted-syntax` with justification.

**Card:**
```tsx
<Card>
  <CardHeader>
    <CardTitle>Title</CardTitle>
    <CardDescription>Description</CardDescription>
  </CardHeader>
  <CardContent>Content</CardContent>
  <CardFooter>Footer</CardFooter>
</Card>
```

**Dialog:**
```tsx
<Dialog>
  <DialogTrigger asChild>
    <Button>Open</Button>
  </DialogTrigger>
  <DialogContent>
    <DialogHeader>
      <DialogTitle>Title</DialogTitle>
      <DialogDescription>Description</DialogDescription>
    </DialogHeader>
    {/* Content */}
    <DialogFooter>
      <Button>Submit</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```

**Toast (Sonner):**
```tsx
import { toast } from "sonner";

toast.success("Video added!");
toast.error("Failed to process");
toast.promise(submitVideo(url), {
  loading: "Processing...",
  success: "Done!",
  error: "Failed",
});
```

### Best Practices

1. Use `asChild` for custom triggers
2. Always handle loading states
3. Compose, don't configure
4. Use semantic color variables

---

# Performance

## React Compiler

The app uses `babel-plugin-react-compiler` for automatic memoization. This eliminates the need for most manual `useMemo`, `useCallback`, and `memo()` calls.

- Configured in `vite.config.ts` via `react({ babel: { plugins: [["babel-plugin-react-compiler"]] } })`
- ESLint plugin `eslint-plugin-react-compiler` with `warn` level catches violations
- Only add manual `memo()` when react-scan confirms the compiler missed something

## Performance Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| `useMemo` for stable array refs | SidebarSection.tsx | `?? []` creates new ref each render, breaks child memo |
| `useCallback` for DnD handlers | DndProvider.tsx | Event handlers recreated on re-render without it |
| `createPortal` for DragOverlay | DndProvider.tsx | Avoids re-render cascade through sidebar tree |
| `content-visibility: auto` | index.css (`[data-slot="article-section"]`) | Skips rendering off-screen chapter sections |
| `fetchPriority="high"` | VideoHero.tsx (thumbnail) | LCP image must not use `loading="lazy"` |
| Finite CSS animations | index.css (`breathe`, `pulse-ring`) | Infinite animations waste GPU cycles |
| View Transitions API | theme-provider.tsx | Single GPU crossfade vs per-element transitions |
| Specific CSS transitions | Sidebar components | `transition-[props]` instead of `transition-all` |

---

# State Management

## Categories

| Type             | Example                    | Solution        | Persistence  |
| ---------------- | -------------------------- | --------------- | ------------ |
| **Remote State** | User data, videos, folders | React Query     | API cache    |
| **Local State**  | Auth tokens, theme         | Zustand         | localStorage |
| **UI State**     | Modal open, loading        | useState        | Ephemeral    |
| **Form State**   | Input values, validation   | Controlled inputs + Zod | Ephemeral |
| **URL State**    | Filters, pagination        | URL params      | Shareable    |

## Remote State (React Query)

### Query Keys Factory

```typescript
// src/lib/query-keys.ts
export const queryKeys = {
  videos: {
    all: ["videos"] as const,
    lists: () => [...queryKeys.videos.all, "list"] as const,
    list: (folderId?: string) => [...queryKeys.videos.lists(), { folderId }] as const,
    details: () => [...queryKeys.videos.all, "detail"] as const,
    detail: (id: string) => [...queryKeys.videos.details(), id] as const,
  },
  user: {
    current: ["user", "current"] as const,
  },
  folders: {
    all: ["folders"] as const,
    list: () => [...queryKeys.folders.all, "list"] as const,
  },
} as const;
```

### Custom Query Hooks

```typescript
// src/hooks/use-videos.ts
export function useVideos(folderId?: string) {
  return useQuery({
    queryKey: queryKeys.videos.list(folderId),
    queryFn: () => api.videos.list(folderId),
  });
}

export function useAddVideo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (url: string) => api.videos.create(url),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
    },
  });
}
```

## Local State (Zustand)

### Auth Store

```typescript
// src/stores/auth-store.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  setAuth: (user: User, token: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      setAuth: (user, accessToken) => set({ user, accessToken, isAuthenticated: true }),
      logout: () => set({ user: null, accessToken: null, isAuthenticated: false }),
    }),
    { name: "vie-auth" }
  )
);
```

### Selectors (Prevent Re-renders)

```typescript
// DO: Atomic selectors
const user = useAuthStore((state) => state.user);
const logout = useAuthStore((state) => state.logout);

// DON'T: Destructure entire store
const { user, accessToken, logout } = useAuthStore(); // Re-renders on ANY change
```

## Form State (Controlled Inputs)

React Hook Form is **not installed** (removed in the 2026-07 dead-deps cleanup). Forms are plain controlled inputs with `useState` + explicit validation on submit — see `features/video-output/components/VideoIntakeForm.tsx` for the canonical pattern:

```tsx
function AddVideoForm() {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!isValidYouTubeUrl(url)) {
      setError("Must be a YouTube URL");
      return;
    }
    setError(null);
    onSubmit(url);
  };

  return (
    <form onSubmit={handleSubmit}>
      <Input value={url} onChange={(e) => setUrl(e.target.value)}
             placeholder="https://youtube.com/watch?v=..." />
      {error && <p className="text-destructive text-sm">{error}</p>}
      <Button type="submit">Submit</Button>
    </form>
  );
}
```

Zod is used at data boundaries (SSE payloads via `sse-validators.ts`, tab props via `tab-prop-schemas.ts`), not for form state.

---

# Composable Output System (v2)

The output system renders video summaries using component-addressed tabs assembled by the backend.

## Architecture

```
Backend Assembly → TabEntry[] → SSE tab_ready events
                                      ↓
OutputRouter → ComposableOutput → COMPONENT_REGISTRY → Interactive Renderers
              (v2 path)                                  (29 components)
                                      or
              ComposableOutputV1 → resolveTabData → renderInteractive
              (v1 fallback)
```

`COMPONENT_REGISTRY` lives in `output/component-registry.tsx` (`ComposableOutput.tsx` re-exports `REGISTERED_COMPONENT_NAMES` for back-compat).

## Component Layer Model

```
Layer 1: shadcn/ui    (components/ui/)                    — Accessible primitives (Button, Dialog, etc.)
Layer 2: VIE Library  (components/vie/)                   — Domain-free, reusable presentation components
Layer 3: Interactives (output/interactive/)               — Self-contained mini-apps with state
Layer 4: Shell        (features/video-output/components/output/) — State coordination (TabCoordinationContext)
```

**Rules:**
- Layer 2 (vie/) takes only primitive props (string, number, ReactNode) — no `@vie/types`
- Layer 2 components are all wrapped in `React.memo()`
- Import vie/ components via barrel: `import { GlassCard, ScoreRing } from '@/components/vie'`
- Layer 3 interactives compose from Layer 2 components

### Key Components

| Component | Purpose |
|-----------|---------|
| `OutputRouter` | Routes to v2 (assembledTabs) or v1 (triage + extraction) path |
| `ComposableOutput` | Maps `tab.component` to `COMPONENT_REGISTRY` renderer |
| `ComposableOutputV1` | Legacy fallback: `resolveTabData()` + `renderInteractive()` |
| `DisplaySection` | Data-driven section renderer for simple data display |
| `TabLayout` | Tab shell with navigation, progress, celebrations |
| `TabCoordinationContext` | Cross-tab state (activeTab, completedTabs) |
| `CrossTabLink` | Navigates between related tabs |
| `TabIntro` | The tab's goal line, printed on every tab that has a `goal` (outside an active flow mode). `ComposableOutput` first drops a `summary_header` attachment whose text only repeats the goal (`withoutGoalEcho`; `lineKey` = the summarizer's `_same_line`: whitespace collapsed, trailing `.!?` dropped, case-folded) — rows assembled before output-quality 0b.6 carry one — so the goal prints once |
| `SeekChip` (`components/vie/data/`) | The shared accent timestamp chip (spot cards, quiz explanations): hour-aware via `formatDuration`; a button calling `onSeek` when one is passed, else a static marker |
| `VideoHero` (+ `VideoPlayerContext`) | Hosts the **inline** YouTube player (the old `CollapsibleVideoPlayer` was deleted). Context exposes `seekTo`, `registerPlayerAnchor` (scroll-into-view on seek), a `hasEngaged` latch, and Escape-to-close (capture-aware, skips typing targets). |
| `VideoHero` blocks (`components/vie/cards/`, output-quality 2.3) | Top to bottom: the identity strip (domain · level · time · creator), the title, the **tldr** (the thesis), `HeroBrief` (a collapsible "Brief": the text is `brief` when it is non-blank, else `masterSummary` — rows stored before the hero rewrite carry only the latter; open on desktop, collapsed on phones), `HeroTakeaways`, `HeroWorthIt`, then the tab strip. With `readingMinutes` set the identity strip reads **"Read: N min · Watch: M min"** (M = the duration in whole minutes, omitted without a duration) in place of the bare duration; without it the duration chip stays. Labels `worthItFor`, `skipIf`, `readMinutes`, `watchMinutes` (`{minutes}` interpolated) are in `lib/i18n.ts` for en / he / ar |
| `HeroTakeaways` + `takeaway-stamps.ts` | `splitTakeawayStamps` splits each takeaway into its claim and the closing `[m:ss]` / `[h:mm:ss]` marker(s) (minutes and seconds `00–59`, so `[12:60]` stays text); every marker renders as a ▶ `SeekChip` on the player's `seekTo` and never as text — for memory's preview takeaways and the rewritten hero's alike; a takeaway without a marker is plain text. A chip seek sets the list open (`handleSeek`), so it stays open for the next chip (the one-shot auto-collapse is for engagement elsewhere). Open on load on desktop (at most 7, the rest behind "+N"), collapsed on phones (3 shown) |
| `HeroWorthIt` | "Worth it for" / "Skip if" (`worthItFor`, `skipIf`, ≤ 20 words each, from the hero rewrite): a `<dl>` below the takeaways — side by side from the `sm` breakpoint, stacked on phones, where it sits under the collapsed takeaways. A blank line is not rendered; with both blank the block is absent. Where the hero's fields come from: while streaming `handleSynthesisComplete` merges `synthesis_complete` into `state.synthesis`; on stored pages (detail page, `SharePage`) `buildSynthesisFromMeta` copies `brief`, `worthItFor`, `skipIf`, `readingMinutes` from `meta` only when set (non-empty string, finite positive number); `CommandDeck` (`OutputTabShell.tsx`) passes them to `VideoHero`. A row without them renders as before |
| `StreamingPlaceholder` / `StreamErrorCard` | Pre-tab streaming states and stream-failure card with retry (replaced `OutputSkeleton`) |
| `TabProgressStrip` / `ProvisionalTabNotice` | In-tab streaming chrome: progress, stage row and Cancel above the panel; the "Early preview" header of a provisional tab |
| `RecipePlayer` | Cooking mode: ingredient panel + step-by-step player |

### COMPONENT_REGISTRY (29 Renderers)

Each tab's `component` field maps to a renderer in `output/component-registry.tsx`. Legacy v1 keys (`quiz`, `code_explorer`, `exercise_tracker`, `scenario`, `verdict`, `gallery`, `lyrics_player`, `timeline`, `clip_player`) were retired in interactive-overhaul-v2 — cached rows with those keys fall through to `display_section`.

**Primary tab renderers (23):**

| Component Name | Renderer | Description |
|---------------|----------|-------------|
| `overview` | OverviewInteractive | Takeaways, highlights, tips, sibling-tab nav grid |
| `info_grid` | InfoGridInteractive | Key/value grid with sections, per-item frame evidence, headline chips |
| `checklist` | ChecklistInteractive | Checkbox items, serving scaler, grouped items |
| `step_player` | StepByStepInteractive | Progress tracking, timers, video sync |
| `comparison` | ComparisonInteractive | Per-row winner highlighting, verdict ScoreRing, pros/cons (with counts) |
| `comparison_radar` | ComparisonInteractive (`view="radar"`) | Same renderer with the radar hero forced on — still only with ≥ 3 numeric axes (both cells numbers, ordinals excluded: `radar-scoring.ts`, twin of the summarizer's `derived_guard.is_numeric_axis`) |
| `flash_deck` | FlashDeckInteractive | Keyboard + touch swipe, shuffleable |
| `spot_explorer` | SpotExplorer | Location spots with sections, "All" pill, grid mode (>8 spots with frames), pronunciation; a spot's `timestamp` renders a `SeekChip`, its `quote` an italic line |
| `budget` | BudgetInteractive | Editable amounts, SVG donut chart, savings calculator |
| `moment_track` | MomentTrack | **Value gallery** (not a nav strip): grid/timeline view toggle; the default follows the items (timeline when ≥ 50 % carry a description the row shows, else grid) and a toggle choice holds for the tab, never persisted. Clicking a card opens the `Lightbox` (never seeks); the explicit **Jump** pill seeks. Timeline mode uses the `use-focus-band` scroll focus band and shows descriptions unclamped. Split into `MomentGalleryCard` / `MomentTimelineRow` / `moment-utils`. |
| `video_filmstrip` | VideoFilmstrip (`mode="tab"`) | Enriched scrubber: wide cells, visible captions, whole-cell seek. The filmstrip tab is **suppressed by OutputRouter** when a `moment_track` tab has ≥8 frame-backed items. |
| `code_playground` | CodePlayground | Tech snippets with run/navigate modes |
| `quiz_arena` | QuizArena | Quiz flow (educational domains only — see `domains.json` `forbidden`); untimed unless `withTimer` (default false); each `(m:ss)` in an explanation renders as a `SeekChip` (`QuizExplanation`) |
| `packing_mission` | PackingMission | Gamified packing checklist |
| `workout_room` | WorkoutRoom | Exercises + warmup/cooldown with seek |
| `lyrics_karaoke` | LyricsKaraoke | Per-line synced karaoke view |
| `claims_tracker` | ClaimsTracker | News claims with evidence citations |
| `tier_list` | TierList | Ranked tiers (single-hue ramp) |
| `formation_diagram` | FormationDiagram | Read-only React Flow pitch (lazy-loaded) |
| `concept_canvas` | ConceptCanvas | Opens as a list on every viewport (`ConceptCanvasList`: definition, example, seek chip inline); the Dagre-laid grouped map behind `CanvasViewToggle`; docked inspector; typed edge relations (lazy-loaded) |
| `step_flow_canvas` | StepFlowCanvas | Opens as a step list on every viewport (instruction, tips, safety note, seek chip); the step graph behind `CanvasViewToggle`, sharing `step_player`'s completion state (lazy-loaded) |
| `connect_canvas` | ConnectCanvas | Graded match-the-pairs canvas (lazy-loaded) |
| `display_section` | DisplaySection | Generic data-driven fallback renderer |

**Secondary / attachment renderers (6)** — rendered by `TabAttachments` around a primary, never as standalone tabs:

| Component Name | Renderer | Description |
|---------------|----------|-------------|
| `stat_banner` | StatBanner | Stat pill row |
| `tip_callout` | TipCallout | Tip/warning/note callout |
| `summary_header` | SummaryHeader | One content line on a dense tab (the extraction summary or the first item, never the goal); hidden when it repeats the goal (see `TabIntro`) |
| `diagram_card` | DiagramCard | Small node/edge diagram (lazy-loaded) |
| `frame_strip` | VideoFilmstrip (`mode="overlay"`) | Thin frame strip overlay |
| `quick_quiz` | QuizArena (single question) | One-question quiz attachment |

> The canvas renderers (`concept_canvas`, `step_flow_canvas`, `connect_canvas`,
> `formation_diagram`, `diagram_card`) are `React.lazy`-loaded and built on the
> shared Canvas System below.

### Canvas System (React Flow)

`apps/web/src/components/vie/canvas/` is the shared graph layer behind the canvas
renderers. Dependency: `@dagrejs/dagre` (^3.0.0).

| File | Role |
|------|------|
| `CanvasShell.tsx` | React Flow wrapper — bounded pan, locked (non-draggable) nodes, registered `edgeTypes`, a `minZoom` floor raised by `nodeWidth` (`clampMinZoom`) so a node card never renders under 120 px; also exports `CanvasViewToggle`, the list ↔ graph switch. **Nodes must keep hidden handles** even with floating edges, or RF errors and renders zero edges. |
| `FloatingEdge.tsx` | Handle-less edge renderer; line style encodes the relation, never color (`causes`/`requires` → solid + arrow, `contrasts` → dashed, `partOf`/`relatesTo` → dotted). |
| `useGraphLayout.ts` | Deterministic Dagre layout — tiers concepts within a group, lays groups out as horizontal lanes; pure + SSR-safe. |
| `CanvasInspector.tsx` | Right-dock (desktop) / bottom-sheet (mobile) detail panel — concept definition, neighbors, frame evidence. Rendered as a docked sibling **outside** React Flow so it is never z-trapped behind nodes. |

`ConceptCanvas` and `StepFlowCanvas` open as lists on every viewport and consume these in
their graph views — `ConceptCanvas`'s map is a static (non-drag) grouped graph from
`ConceptCanvasProps` (`concepts`, optional `groups` for lane order). See
[DATA-MODELS.md](./DATA-MODELS.md#interactive-component-types) for the prop types.

### Data Flow

```
1. SSE triage_complete → domain accent binds, pending-tab strip appears (VideoHero)
2. SSE tab_ready[]     → tabs render progressively (contract: API-REFERENCE.md#tab_ready):
                         plan-done → the provisional Moments tab ("Early preview"
                         notice + a dot in the strip); split extraction (default)
                         → a non-final preview per tab as its extraction group
                         lands (no links/attachments yet), then the overview and
                         whatever assembly added; EXTRACTION_PARALLEL=false →
                         assembly sends the rest, non-final while a late quiz is
                         pending; the framed Moments tab replaces the provisional
                         by id; the final re-emit sends every tab `final: true`
                         at its exact position (split path: the overview last)
3. SSE complete        → `tabIds` = the final order; streamed tabs missing from it
                         are withdrawn
4. SSE done            → celebration; the page refetches the doc
```

The reducer (`lib/streaming/stream-event-processor.ts` + `stream-tabs.ts`)
keys tabs by id and keeps them sorted by (`position`, final first, arrival) —
`position` is a sort key, never a splice index. A later emission replaces the
earlier one; a final tab is replaced only by a later final one, never by a
non-final or provisional replay. `tab_ready.replaces` (optional) names a tab
the incoming one supersedes under another id: it takes that tab's slot and its
focus. `meta` never touches tabs. The first non-provisional tab moves the
timeline to "Build" (assembly sends no phase event); a later group's
`extraction_progress` updates the progress but never moves it back.

While streaming, the page keeps one tree (hero + tab strip mounted from the
first event, so nothing remounts when tabs land): before the first tab the
`StreamingPlaceholder` card; after it `TabProgressStrip` (Generating N of M,
the stages in one row, Cancel — rendered only while the stream is live) above
the panel, and the planned tabs that haven't landed as inert pills after the
live ones. A stream error after tabs landed (e.g. a dropped connection) shows
`StreamErrorCard` above the tabs; its Retry reattaches to the stream (the run
keeps going server-side and the stream replays from its first event).

`lib/streaming/resolve-display-tabs.ts` decides between the DB document and
the stream by the stream's `final` state, not tab count: while processing the
stream wins; on a completed video an **unsettled** stream (dropped connection,
a withdrawn tab still held, any cached replay) yields to the DB doc, and a
**settled** live run (`complete.tabIds` all received final) keeps its tab
objects wherever the DB copy renders the same content — frame-URL signatures
aside (both non-empty), since every DB serve re-signs them and swapping would
reload every frame image. A cached replay never settles: its frame URLs were
signed for the run that produced the doc and may have expired. Anything the DB holds differently comes from the DB. The
original-language toggle always reads `video.sourceLanguage` (DB only).

### Tab Coordination

`TabCoordinationContext` manages:
- Active tab state — the initial tab is sticky: the first one chosen stays
  active while later tabs stream in ahead of it (a late tab never steals
  focus); focus on a tab superseded via `replaces` follows its successor; a
  withdrawn active tab falls back to the first visible tab (`TabLayout`)
- Completed tabs tracking (persisted in sessionStorage per videoId)
- Cross-tab navigation via `CrossTabLink`

---

## RAG Components

### RAGSourceCard

Displays source cards for RAG-based explanations.

```tsx
<RAGSourceCard
  source={{
    videoSummaryId: 'video-1',
    title: 'Source Video',
    timestamp: 120,
    relevanceScore: 0.95,
  }}
  onClick={() => navigateToSource()}
/>
```

### RAGChatPanel

Chat interface for RAG-powered conversations.

```tsx
<RAGChatPanel
  sources={[...]}
  initialMessages={[...]}
  onSendMessage={async (message) => {...}}
  scope="video"   // optional 'video' | 'library' — renders a header chip
/>
```

The optional `scope` prop surfaces a header chip ("This video" / "Your library")
so the user knows the chat's grounding. The Sidebar passes
`scope={activeVideoId ? "video" : "library"}`. Both scopes can still trigger
library actions (create/move/organize) — the chip is informational.

---

# Markdown Rendering

## MarkdownContent Component

Shared markdown renderer used across all chat surfaces. Uses `react-markdown` with `@tailwindcss/typography` prose classes.

**Location:** `src/components/ui/markdown-content.tsx`

### Usage

```tsx
import { MarkdownContent } from "@/components/ui/markdown-content";

// Full mode — drawers, modals, chat (headings, lists, bold, code)
<MarkdownContent content={markdownString} />

// Compact mode — popovers, tooltips (no headings, tighter spacing)
<MarkdownContent content={markdownString} compact />
```

### Consumers

| Component | Mode | Context |
|-----------|------|---------|
| GoDeepDrawer | full | Section expansion drawer |
| MasterSummaryModal | full | Quick Read modal |
| VideoChatPanel | full | Assistant chat messages |
| ConceptHighlighter (TellMeMore) | compact | Concept popover (max-w-sm, max-h-64 scroll) |

### Typography Plugin

Tailwind v4 CSS-first plugin registration in `index.css`:

```css
@plugin "@tailwindcss/typography";
```

This activates `prose prose-sm dark:prose-invert` classes used by MarkdownContent.

---

# AI Integration

## Assistant Chat Streaming

The Vercel AI SDK is **not used** (removed with the dead-deps cleanup). Assistant chat is hand-rolled streaming in `features/sidebar/hooks/use-sidebar-chat.ts`: every message (single-video or library mode) goes through a streamed fetch to the assistant service, with a streaming assistant-message placeholder, tool-call step lines, and one folder/video tree refetch per turn on the chat `done` event. UI panels live in `components/rag/` (`RAGChatPanel`, `RAGSourceCard`).

Summary streaming (the pipeline SSE) is separate — see `features/video-output/lib/streaming/` and the Processing Store State section below.

## WebSocket Connection

```tsx
import { useWebSocket } from "@/hooks/use-websocket";

function App() {
  const { connectionState } = useWebSocket();
  // connectionState: "connecting" | "connected" | "disconnected"

  return (
    <div>
      {connectionState !== "connected" && (
        <Badge variant="outline">
          {connectionState === "connecting" ? "Reconnecting..." : "Offline"}
        </Badge>
      )}
    </div>
  );
}
```

### WebSocket Events

| Event Type | Payload | Action |
|------------|---------|--------|
| `video.status` | `{ videoSummaryId, status, progress?, error? }` | Invalidates video list queries |
| `video.metadata` | `{ videoSummaryId, title, channel?, thumbnailUrl?, duration? }` | Invalidates video list for sidebar title sync |

## Processing Manager (Auto-Resume)

The `useProcessingManager` hook provides app-level management of video processing streams. It enables:

1. **Auto-resume after browser refresh**: Automatically reconnects to SSE streams for any videos still processing
2. **Sidebar title sync**: WebSocket broadcasts metadata updates for real-time title display
3. **Centralized stream state**: Processing state shared across components via Zustand store

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  App.tsx                                                    │
│  ├── useWebSocket()      - Real-time status/metadata events │
│  └── useProcessingManager() - Auto-manages SSE streams      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  processing-store.ts (Zustand)                              │
│  └── streamStates: Map<videoSummaryId, ProcessingStreamState> │
└─────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
        ┌───────────────┐          ┌───────────────────┐
        │ Sidebar       │          │ VideoDetailPage   │
        │ (spinner)     │          │ (full progress)   │
        └───────────────┘          └───────────────────┘
```

### Usage

The hook is initialized in `App.tsx` after `useWebSocket()`:

```tsx
function AppRoutes() {
  useWebSocket();          // Real-time updates
  useProcessingManager();  // Auto-resume & sidebar sync
  // ...
}
```

### Processing Store State

```typescript
interface ProcessingStreamState {
  phase: StreamPhase;
  // StreamPhase = "idle" | "connecting" | "metadata" | "transcript" | "extraction"
  //             | "building" | "translation" | "done" | "cancelled" | "error"
  // Raw SSE phases (transcript_cached, whisper_transcription, translation, …)
  // are collapsed to this UI vocabulary in stream-event-processor.ts
  // (SSE_PHASE_MAP), with the flavor preserved in StreamState.phaseDetail.
  metadata: {
    title?: string;
    channel?: string;
    thumbnailUrl?: string;
    duration?: number;
  } | null;
  sectionsCount: number;
  error: string | null;
}
```

### How It Works

1. **Watch video list**: When videos with status `pending` or `processing` are detected
2. **Start SSE streams**: Automatically connects to `/api/videos/:id/stream` for each
3. **Update store**: Stream events update `processing-store` state
4. **Cleanup**: Streams are aborted when videos complete, are deleted, or user logs out

---

# Commands

```bash
# Development
pnpm run dev

# Build
pnpm run build

# Preview build
pnpm run preview

# Type check
pnpm run typecheck

# Lint
pnpm run lint
```

---

# Dev Tools (Development Only)

The frontend includes a dev-only panel for testing LLM providers.

## DevToolPanel

Located at `src/components/dev/DevToolPanel.tsx`, only visible when `import.meta.env.DEV === true`.

### Features

- **Provider Selection**: Choose between anthropic, openai, gemini for default/fast/fallback
- **Bypass Cache**: Toggle to force re-summarization (test both cached and non-cached paths)
- **Re-summarize**: Trigger new summarization with selected provider configuration

### Usage

1. Start dev server: `pnpm run dev`
2. Look for "Dev Tools" at bottom of sidebar
3. Expand panel, enter a video URL
4. Select providers and cache settings
5. Click "Re-summarize"
6. Check summarizer logs for provider selection

### Files

| File | Purpose |
|------|---------|
| `components/dev/ProviderSelector.tsx` | Provider dropdown component |
| `components/dev/DevToolPanel.tsx` | Main dev tools panel |

### Production

The dev panel is tree-shaken from production builds via:
```tsx
const DevToolPanel = import.meta.env.DEV
  ? lazy(() => import("@/components/dev/DevToolPanel")...)
  : null;
```

## Design System Page (`/dev/design-system`)

Living style guide for all design tokens and components.

| Section | Contents |
|---------|----------|
| Color Palette | Semantic colors (background, foreground, primary, etc.) with swatches |
| Typography | Text scale (xs-4xl) and font weights |
| Spacing Scale | Tailwind spacing tokens (1-12) with visual boxes |
| Cards Showcase | GlassCard, ExpandableCard, HeroCard, ImageCard, VideoHero |
| Interactive Block Showcase | All interactive renderers with live previews |
| Modes | FlowPlayer "enter mode" demos (cooking, workout, build, study, explore, practice, listen) — extracted from the Interactive showcase |
| UI Showcase | Buttons, badges, dialogs, toasts, form elements |
| VIE Library Showcase | All Layer 2 components: data, content, navigation, feedback, media |

### Dev Page Files

| File | Purpose |
|------|---------|
| `pages/dev/DesignSystemPage.tsx` | Design system page |
| `components/dev/design-system/ColorPalette.tsx` | Color token showcase |
| `components/dev/design-system/Typography.tsx` | Typography showcase |
| `components/dev/design-system/SpacingScale.tsx` | Spacing showcase |
| `components/dev/design-system/CardsShowcase.tsx` | Card component showcase |
| `components/dev/design-system/InteractiveBlockShowcase.tsx` | Interactive renderer showcase |
| `components/dev/design-system/ModesShowcase.tsx` | FlowPlayer "enter mode" demos (Modes tab) |
| `components/dev/design-system/showcase-primitives.tsx` | Shared dev-demo primitives (`SectionCard`, `DemoBoundary`, `DemoSuspenseFallback`, `ShowcaseEntry`) |
| `components/dev/design-system/UIShowcase.tsx` | UI primitives showcase |
| `components/dev/design-system/VIELibraryShowcase.tsx` | VIE component library showcase |
| `lib/dev/mock-interactive-blocks.ts` | Mock data for interactive components |

## Streaming Preview Page (`/dev/streaming-preview`)

DEV-only harness (`pages/dev/StreamingPreviewPage.tsx`, lazy-loaded) that drives
`StreamingPlaceholder`, `StreamErrorCard`, and `VideoHero` through canned
streaming states — useful for iterating on pre-tab UI without running the pipeline.

### Production Safety

All dev code has production guards:
```tsx
if (!import.meta.env.DEV) {
  throw new Error('This module should not be imported in production');
}
```

---

# Anti-Patterns to Avoid

| Anti-Pattern | Fix |
|--------------|-----|
| Duplicate remote state in local state | Use React Query directly |
| Destructure entire Zustand store | Use atomic selectors |
| Store derived state | Compute during render |
| Fetch in useEffect | Use React Query |
| Hardcode colors | Use semantic variables |
| `transition-all` | Be specific: `transition-colors` |
| Dynamic class construction | Use complete class strings |
| Missing focus states | Always include `focus-visible:ring` |
