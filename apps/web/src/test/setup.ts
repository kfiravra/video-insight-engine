import "@testing-library/jest-dom";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { server } from "./mocks/server";

// Start MSW server before all tests
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

// Reset handlers after each test (important for test isolation)
afterEach(() => {
  server.resetHandlers();
});

// Clean up after all tests
afterAll(() => {
  server.close();
});

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
    get length() {
      return Object.keys(store).length;
    },
    key: vi.fn((index: number) => Object.keys(store)[index] ?? null),
  };
})();

Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
});

// Mock matchMedia. Default `prefers-reduced-motion: reduce` to true so
// useCountUp/animated components jump to their final state immediately —
// most assertions target post-animation values. A test that needs to
// exercise the animating path can call `setPrefersReducedMotionForTest(false)`
// in a beforeEach; the value resets to true after every test.
const matchMediaState: { prefersReducedMotion: boolean } = {
  prefersReducedMotion: true,
};

export function setPrefersReducedMotionForTest(value: boolean): void {
  matchMediaState.prefersReducedMotion = value;
}

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: /prefers-reduced-motion/.test(query)
      ? matchMediaState.prefersReducedMotion
      : false,
    media: query,
    onchange: null,
    addListener: vi.fn(), // deprecated
    removeListener: vi.fn(), // deprecated
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock ResizeObserver
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

Object.defineProperty(window, "ResizeObserver", {
  value: ResizeObserverMock,
});

// Mock IntersectionObserver
class IntersectionObserverMock {
  readonly root: Element | null = null;
  readonly rootMargin: string = "";
  readonly thresholds: ReadonlyArray<number> = [];
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn().mockReturnValue([]);
}

// writable+configurable so individual tests can swap in
// a controllable observer and restore this inert default afterwards.
Object.defineProperty(window, "IntersectionObserver", {
  writable: true,
  configurable: true,
  value: IntersectionObserverMock,
});

// Mock scrollTo
Object.defineProperty(window, "scrollTo", {
  value: vi.fn(),
});

// jsdom does not implement scrollIntoView; VideoPlayerContext.seekTo calls it
// on the registered player anchor, so any test rendering a player consumer
// would otherwise throw on seek.
Element.prototype.scrollIntoView = vi.fn();

// Reset localStorage mock and matchMedia override before each test
afterEach(async () => {
  localStorageMock.clear();
  matchMediaState.prefersReducedMotion = true;
  vi.clearAllMocks();
  // Clear the SSE stream registry between tests so a stream from one test
  // (which would otherwise persist as a module-level singleton) doesn't
  // bleed into the next.
  const { abortAllStreams } = await import(
    "../features/video-output/lib/streaming/stream-registry"
  );
  abortAllStreams();
});
