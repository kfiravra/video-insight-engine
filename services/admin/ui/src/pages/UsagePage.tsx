import { CostChart } from '../components/CostChart';
import { FeatureBreakdown } from '../components/FeatureBreakdown';
import { ModelBreakdown } from '../components/ModelBreakdown';
import { RecentCalls } from '../components/RecentCalls';
import { PipelineRunsPanel } from '../components/PipelineRunsPanel';
import { SessionsPanel } from '../components/SessionsPanel';
import { LatencyPanel } from '../components/LatencyPanel';
import { AnomaliesPanel } from '../components/AnomaliesPanel';
import { DuplicatesPanel } from '../components/DuplicatesPanel';

interface UsagePageProps {
  days?: number;
}

export function UsagePage({ days = 30 }: UsagePageProps = {}) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <CostChart days={days} />
        <ModelBreakdown days={days} />
      </div>
      <FeatureBreakdown days={days} />
      <LatencyPanel days={days} />
      <PipelineRunsPanel days={days} />
      <SessionsPanel days={days} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <AnomaliesPanel days={Math.min(days, 90)} />
        <DuplicatesPanel days={Math.min(days, 90)} />
      </div>
      <RecentCalls />
    </div>
  );
}
