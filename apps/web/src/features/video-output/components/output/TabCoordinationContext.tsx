import { createContext, useContext, useState, useCallback, useEffect, useMemo, type ReactNode } from 'react';

interface TabCoordinationState {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  completedTabs: Set<string>;
  markTabCompleted: (tab: string) => void;
}

const TabCoordinationContext = createContext<TabCoordinationState | null>(null);

interface TabCoordinationProviderProps {
  videoId: string;
  /** The tab to open on. Sticky: the first non-empty value is applied once;
   *  later values (a tab streamed in ahead of it) never steal focus. */
  initialTab: string;
  /** Streamed tab id → the id that superseded it: focus on a superseded tab
   *  follows it to its replacement. */
  replacedTabIds?: Record<string, string>;
  children: ReactNode;
}

const STORAGE_PREFIX = 'vie-completed-tabs-';

function loadCompletedTabs(videoId: string): Set<string> {
  try {
    const raw = sessionStorage.getItem(`${STORAGE_PREFIX}${videoId}`);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((x: unknown) => typeof x === 'string')) return new Set(parsed as string[]);
    }
  } catch {
    // Storage unavailable (private mode) or corrupt — start with nothing completed.
  }
  return new Set();
}

function saveCompletedTabs(videoId: string, tabs: Set<string>) {
  try {
    sessionStorage.setItem(`${STORAGE_PREFIX}${videoId}`, JSON.stringify(Array.from(tabs)));
  } catch {
    // Storage unavailable — completion stays in memory for this view only.
  }
}

/** Follow a chain of `replaces` hops to the tab that holds the slot now. */
function followReplacements(tab: string, replacedTabIds: Record<string, string>): string {
  let current = tab;
  const seen = new Set<string>();
  while (replacedTabIds[current] && !seen.has(current)) {
    seen.add(current);
    current = replacedTabIds[current];
  }
  return current;
}

const NO_REPLACEMENTS: Record<string, string> = {};

/**
 * Provider for cross-tab coordination state.
 * Tracks active tab and completed tabs, persisted in sessionStorage per videoId.
 */
export function TabCoordinationProvider({
  videoId,
  initialTab,
  replacedTabIds = NO_REPLACEMENTS,
  children,
}: TabCoordinationProviderProps) {
  // The tab the user picked, or the initial tab once one existed. State
  // adjusted during render (not in an effect) so the first tab paints active.
  const [chosenTab, setChosenTab] = useState(initialTab);
  const [completedTabs, setCompletedTabs] = useState<Set<string>>(() => loadCompletedTabs(videoId));
  const [trackedVideoId, setTrackedVideoId] = useState(videoId);

  if (trackedVideoId !== videoId) {
    setTrackedVideoId(videoId);
    setChosenTab(initialTab);
    setCompletedTabs(loadCompletedTabs(videoId));
  } else if (!chosenTab && initialTab) {
    setChosenTab(initialTab);
  }

  const activeTab = followReplacements(chosenTab, replacedTabIds);

  const setActiveTab = useCallback((tab: string) => setChosenTab(tab), []);

  const markTabCompleted = useCallback((tab: string) => {
    setCompletedTabs(prev => {
      if (prev.has(tab)) return prev;
      const next = new Set(prev);
      next.add(tab);
      return next;
    });
  }, []);

  // Persist completedTabs to sessionStorage
  useEffect(() => {
    saveCompletedTabs(videoId, completedTabs);
  }, [videoId, completedTabs]);

  const value = useMemo<TabCoordinationState>(() => ({
    activeTab, setActiveTab, completedTabs, markTabCompleted,
  }), [activeTab, setActiveTab, completedTabs, markTabCompleted]);

  return (
    <TabCoordinationContext.Provider value={value}>
      {children}
    </TabCoordinationContext.Provider>
  );
}

/**
 * Hook to access tab coordination state.
 * Returns null when used outside a TabCoordinationProvider.
 */
// eslint-disable-next-line react-refresh/only-export-components -- context + its hook live together; consumers import both from here
export function useTabCoordination(): TabCoordinationState | null {
  return useContext(TabCoordinationContext);
}
