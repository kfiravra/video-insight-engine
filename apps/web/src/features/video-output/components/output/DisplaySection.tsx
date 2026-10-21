import { memo, type ReactNode } from 'react';
import { GlassCard } from './GlassCard';
import {
  Callout,
  KeyValue,
  QuoteBlock,
  ListItems,
  CodeSnippet,
} from '@/components/vie';
import {
  isKeyPoints,
  isAnalysisItems,
  isMusicSections,
  isMusicCredits,
  isLyrics,
  isTechPatterns,
  isTechSetup,
  isBudget,
  isTips,
  isSpecs,
  isQuotes,
  isStringArray,
  mapTipType,
  formatLabel,
  formatTimestamp,
} from './display-type-guards';
import type {
  KeyPointItem,
  TipItem,
  ReviewSpec,
  NarrativeQuote,
  TechSetup as TechSetupType,
  TechPattern,
  MusicSection,
  MusicCredit,
  TravelBudget,
} from '@vie/types';

// ─── Sub-renderers (each ≤50 lines) ───

function KeyPointsSection({ data }: { data: KeyPointItem[] }) {
  return (
    <div className="flex flex-col gap-3">
      {data.map((point, i) => (
        <GlassCard key={i} variant="interactive">
          <div className="flex gap-3">
            <span className="text-xl" aria-hidden="true">{point.emoji}</span>
            <div className="flex-1 min-w-0">
              <h4 className="font-medium text-sm">{point.title}</h4>
              <p className="text-sm text-muted-foreground mt-1">{point.detail}</p>
            </div>
          </div>
        </GlassCard>
      ))}
    </div>
  );
}

function AnalysisSection({ data }: { data: Array<{ aspect: string; emoji?: string; detail: string }> }) {
  return (
    <div className="flex flex-col gap-3">
      {data.map((item, i) => (
        <GlassCard key={i} variant="interactive">
          <div className="flex gap-3">
            {item.emoji && <span className="text-xl" aria-hidden="true">{item.emoji}</span>}
            <div className="flex-1 min-w-0">
              <h4 className="font-medium text-sm">{item.aspect}</h4>
              <p className="text-sm text-muted-foreground mt-1">{item.detail}</p>
            </div>
          </div>
        </GlassCard>
      ))}
    </div>
  );
}

function MusicSectionsDisplay({ data }: { data: MusicSection[] }) {
  return (
    <div className="flex flex-col gap-3">
      {data.map((section, i) => (
        <GlassCard key={i} variant="interactive">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h4 className="font-medium text-sm">{section.name}</h4>
              {section.timestamp != null && (
                <span className="text-xs text-muted-foreground tabular-nums">
                  {formatTimestamp(section.timestamp)}
                </span>
              )}
            </div>
            <p className="text-sm text-muted-foreground">{section.description}</p>
          </div>
        </GlassCard>
      ))}
    </div>
  );
}

function MusicCreditsDisplay({ data }: { data: MusicCredit[] }) {
  return (
    <GlassCard>
      <KeyValue variant="info" items={data.map((c) => ({ key: c.role, value: c.name }))} />
    </GlassCard>
  );
}

function LyricsDisplay({ data }: { data: Array<{ line: string; timestamp?: number }> }) {
  return (
    <GlassCard>
      <div className="space-y-1">
        {data.map((item, i) => (
          <div key={i} className="flex gap-3">
            {item.timestamp != null && (
              <span className="text-xs text-muted-foreground tabular-nums w-10 shrink-0 pt-0.5">
                {formatTimestamp(item.timestamp)}
              </span>
            )}
            <p className="text-sm text-muted-foreground">{item.line}</p>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}

function TechPatternsDisplay({ data }: { data: TechPattern[] }) {
  return (
    <div className="flex flex-col gap-3">
      {data.map((pattern, i) => (
        <GlassCard key={i} variant="interactive">
          <div className="space-y-3">
            <h4 className="font-medium text-sm">{pattern.title}</h4>
            <p className="text-sm text-muted-foreground">{pattern.explanation}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div className="rounded-lg bg-success/[0.05] p-3">
                <span className="text-xs font-medium text-success block mb-1">Do</span>
                <CodeSnippet code={pattern.doExample} />
              </div>
              <div className="rounded-lg bg-destructive/[0.05] p-3">
                <span className="text-xs font-medium text-destructive block mb-1">Don't</span>
                <CodeSnippet code={pattern.dontExample} />
              </div>
            </div>
          </div>
        </GlassCard>
      ))}
    </div>
  );
}

function TechSetupDisplay({ data }: { data: TechSetupType }) {
  return (
    <div className="flex flex-col gap-3">
      {data.commands.length > 0 && (
        <GlassCard>
          <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Commands</h4>
          <div className="space-y-2">
            {data.commands.map((cmd, i) => <CodeSnippet key={i} code={cmd} />)}
          </div>
        </GlassCard>
      )}
      {data.dependencies.length > 0 && (
        <GlassCard>
          <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Dependencies</h4>
          <div className="flex flex-wrap gap-2">
            {data.dependencies.map((dep, i) => (
              <span key={i} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-primary/10 text-xs font-medium text-foreground">
                {dep.name}{dep.version && <span className="text-muted-foreground">@{dep.version}</span>}
              </span>
            ))}
          </div>
        </GlassCard>
      )}
      {data.envVars.length > 0 && (
        <GlassCard>
          <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Environment Variables</h4>
          <KeyValue
            variant="info"
            items={data.envVars.map((v) => ({
              key: v.name,
              value: v.description + (v.example ? ` (e.g. ${v.example})` : ''),
            }))}
          />
        </GlassCard>
      )}
    </div>
  );
}

function BudgetDisplay({ data }: { data: TravelBudget }) {
  return (
    <div className="flex flex-col gap-3">
      <GlassCard variant="elevated">
        <div className="text-center">
          <span className="text-2xl font-bold text-foreground">
            {data.currency} {data.total.toLocaleString()}
          </span>
          <p className="text-xs text-muted-foreground mt-1">Estimated Total</p>
        </div>
      </GlassCard>
      {data.breakdown.length > 0 && (
        <GlassCard>
          <KeyValue
            variant="info"
            items={data.breakdown.map((b) => ({
              key: b.category,
              value: `${data.currency} ${b.amount.toLocaleString()}${b.notes ? ` — ${b.notes}` : ''}`,
            }))}
          />
        </GlassCard>
      )}
    </div>
  );
}

function TipsDisplay({ data }: { data: TipItem[] }) {
  return (
    <div className="flex flex-col gap-2">
      {data.map((tip, i) => (
        <GlassCard key={i}>
          <Callout style={mapTipType(tip.type)} text={tip.text} />
        </GlassCard>
      ))}
    </div>
  );
}

function SpecsDisplay({ data }: { data: ReviewSpec[] }) {
  return (
    <GlassCard>
      <KeyValue variant="specs" items={data.map(s => ({ key: s.key, value: s.value }))} />
    </GlassCard>
  );
}

function QuotesDisplay({ data }: { data: NarrativeQuote[] }) {
  return (
    <div className="flex flex-col gap-3">
      {data.map((quote, i) => (
        <GlassCard key={i}>
          {/* A translated line is not the speaker's words (2.0 d, D12): plain prose. */}
          {quote.quoteTranslated ? (
            <p className="text-sm leading-relaxed text-muted-foreground">
              {quote.text}
              {quote.speaker && <span className="ms-1 text-xs font-medium">— {quote.speaker}</span>}
            </p>
          ) : (
            <QuoteBlock text={quote.text} attribution={quote.speaker} variant="speaker" />
          )}
        </GlassCard>
      ))}
    </div>
  );
}

function GenericObjectDisplay({ data }: { data: Record<string, unknown> }) {
  const primitiveEntries = Object.entries(data).filter(
    ([, v]) => v != null && typeof v !== 'object'
  );
  const arrayEntries = Object.entries(data).filter(
    ([, v]) => Array.isArray(v) && (v as unknown[]).length > 0
  );

  if (primitiveEntries.length === 0 && arrayEntries.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      {primitiveEntries.length > 0 && (
        <GlassCard>
          <KeyValue
            variant="info"
            items={primitiveEntries.map(([key, value]) => ({ key: formatLabel(key), value: String(value) }))}
          />
        </GlassCard>
      )}
      {arrayEntries.map(([key, value]) => {
        const arr = value as unknown[];
        if (isStringArray(arr)) {
          return (
            <GlassCard key={key}>
              <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">{formatLabel(key)}</h4>
              <ListItems items={arr} />
            </GlassCard>
          );
        }
        if (isSpecs(arr)) {
          return (
            <GlassCard key={key}>
              <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">{formatLabel(key)}</h4>
              <KeyValue variant="info" items={arr.map(s => ({ key: s.key, value: s.value }))} />
            </GlassCard>
          );
        }
        return null;
      })}
    </div>
  );
}

// ─── Dispatch pipeline ───

/** Type-safe pipeline entry — guard narrows to T, render receives T.
 * The `tryRender` method encapsulates the guard→render flow. */
function pipelineEntry<T>(
  guard: (data: unknown) => data is T,
  render: (data: T) => ReactNode,
): { tryRender: (data: unknown) => ReactNode | null } {
  return {
    tryRender(data: unknown) {
      return guard(data) ? render(data) : null;
    },
  };
}

/** Ordered list of type guards and their renderers. First match wins. */
const DISPLAY_PIPELINE = [
  pipelineEntry(isKeyPoints, (d) => <KeyPointsSection data={d} />),
  pipelineEntry(isAnalysisItems, (d) => <AnalysisSection data={d} />),
  pipelineEntry(isMusicSections, (d) => <MusicSectionsDisplay data={d} />),
  pipelineEntry(isMusicCredits, (d) => <MusicCreditsDisplay data={d} />),
  pipelineEntry(isLyrics, (d) => <LyricsDisplay data={d} />),
  pipelineEntry(isTechPatterns, (d) => <TechPatternsDisplay data={d} />),
  pipelineEntry(isTechSetup, (d) => <TechSetupDisplay data={d} />),
  pipelineEntry(isBudget, (d) => <BudgetDisplay data={d} />),
  pipelineEntry(isTips, (d) => <TipsDisplay data={d} />),
  pipelineEntry(isSpecs, (d) => <SpecsDisplay data={d} />),
  pipelineEntry(isQuotes, (d) => <QuotesDisplay data={d} />),
  pipelineEntry(isStringArray, (d) => <GlassCard><ListItems items={d} /></GlassCard>),
];

interface DisplaySectionProps {
  data: unknown;
}

/**
 * Generic display section for non-interactive tabs.
 * Renders domain data using appropriate sub-renderers based on data shape.
 */
export const DisplaySection = memo(function DisplaySection({ data }: DisplaySectionProps) {
  if (data == null) {
    return (
      <GlassCard>
        <p className="text-sm text-muted-foreground text-center py-4">
          No data available for this section.
        </p>
      </GlassCard>
    );
  }

  // Run through typed dispatch pipeline — each entry encapsulates guard + render
  for (const { tryRender } of DISPLAY_PIPELINE) {
    const result = tryRender(data);
    if (result) return <>{result}</>;
  }

  // Plain string
  if (typeof data === 'string') {
    return (
      <GlassCard>
        <p className="text-sm text-muted-foreground whitespace-pre-wrap leading-relaxed">{data}</p>
      </GlassCard>
    );
  }

  // Generic object fallback
  if (typeof data === 'object' && !Array.isArray(data)) {
    return <GenericObjectDisplay data={data as Record<string, unknown>} />;
  }

  return null;
});
