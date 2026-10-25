import { memo, useCallback, useMemo, useState } from 'react';
import { Network } from 'lucide-react';

import { CanvasViewToggle } from '@/components/vie/canvas/CanvasShell';
import { CanvasInspector } from '@/components/vie/canvas/CanvasInspector';
import { useIsDesktop } from '@/hooks/use-media-query';
import { useLabels } from '@/lib/i18n';
import type { ConceptItem } from '@vie/types';

import { ConceptCanvasList } from './ConceptCanvasList';
import { ConceptMap } from './ConceptMap';
import { EmptyTabState } from './EmptyTabState';
import { buildConceptGraph } from './concept-graph';

// ─── Props ───

interface ConceptCanvasProps {
  concepts: ConceptItem[];
  groups?: string[];
  onSeek?: (seconds: number) => void;
}

type ConceptView = 'list' | 'map';

// ─── Canvas ───

/**
 * Concept canvas. Opens in the list view on every viewport — definition,
 * example and seek chip inline per concept (R8 / D8); the map (`ConceptMap`,
 * Dagre + @xyflow/react) is a toggle and mounts only when chosen. Selecting a
 * concept docks the `CanvasInspector`: inline under the concept in the list,
 * over the canvas in the map.
 */
export const ConceptCanvas = memo(function ConceptCanvas({
  concepts,
  groups: groupsProp,
  onSeek,
}: ConceptCanvasProps) {
  const t = useLabels();
  const isDesktop = useIsDesktop();
  const model = useMemo(() => buildConceptGraph(concepts, groupsProp), [concepts, groupsProp]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<ConceptView>('list');

  const handleSelect = useCallback((id: string) => setSelectedId(id), []);
  // The list's concept names are disclosures: the open one closes again.
  const handleToggle = useCallback(
    (id: string) => setSelectedId((current) => (current === id ? null : id)),
    [],
  );
  const handleClose = useCallback(() => setSelectedId(null), []);

  const selectedConcept = model.nodes.find((n) => n.id === selectedId)?.concept ?? null;
  const selectedNeighbors = selectedId ? model.adjacency.get(selectedId) ?? [] : [];

  if (!model.nodes.length) {
    return <EmptyTabState message="No concepts were extracted for this video." icon={Network} />;
  }

  const counts = `${model.nodes.length} concepts · ${model.groups.length} groups · ${model.edges.length} links`;

  return (
    <div className="space-y-3" data-slot="concept-canvas">
      {/* Header strip: restrained analytics + view toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium tabular-nums text-muted-foreground" data-testid="concept-canvas-counts">
          {counts}
        </p>
        <CanvasViewToggle
          label="Concept view"
          value={view}
          options={[
            { value: 'list', label: t.viewList },
            { value: 'map', label: t.viewMap },
          ]}
          onChange={setView}
        />
      </div>

      {view === 'map' ? (
        <ConceptMap
          model={model}
          selectedId={selectedId}
          onSelect={handleSelect}
          onClose={handleClose}
          onSeek={onSeek}
          isDesktop={isDesktop}
        />
      ) : (
        <ConceptCanvasList
          model={model}
          selectedId={selectedId}
          onToggle={handleToggle}
          onSeek={onSeek}
          inspector={
            selectedConcept && (
              <CanvasInspector
                concept={selectedConcept}
                neighbors={selectedNeighbors}
                onSelectNeighbor={handleSelect}
                onClose={handleClose}
                onSeek={onSeek}
                isDesktop={isDesktop}
                placement="inline"
              />
            )
          }
        />
      )}
    </div>
  );
});
