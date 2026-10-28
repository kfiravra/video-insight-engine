/**
 * Alert presentation helpers shared by AlertsPage and AlertsBanner.
 *
 * Producers (llm-common cost callback, admin alert_evaluator, summarizer stall
 * sweeper) now stamp an explicit `severity`; the per-type table below is the
 * fallback for rows written before that, and the regex is the last resort for
 * unknown types.
 */

export type AlertSeverity = 'critical' | 'warning' | 'info';

export type AlertDoc = Record<string, unknown>;

const SEVERITY_BY_TYPE: Record<string, AlertSeverity> = {
  high_cost_call: 'warning',
  daily_spend_spike: 'warning',
  daily_global_cost: 'critical',
  high_failure_rate: 'critical',
  backup_stale: 'critical',
  pipeline_stalled: 'critical',
};

const CRITICAL_MATCH = /(critical|error|failure|exceed|anomal|stalled|down)/i;
const WARNING_MATCH = /(warn|spike|threshold|degraded)/i;

function isSeverity(value: unknown): value is AlertSeverity {
  return value === 'critical' || value === 'warning' || value === 'info';
}

export function getSeverity(alert: AlertDoc): AlertSeverity {
  const explicit = alert.severity ?? alert.level;
  if (isSeverity(explicit)) return explicit;
  const type = String(alert.type ?? '');
  const byType = SEVERITY_BY_TYPE[type];
  if (byType) return byType;
  if (CRITICAL_MATCH.test(type)) return 'critical';
  if (WARNING_MATCH.test(type)) return 'warning';
  return 'info';
}

export function severityColor(sev: AlertSeverity): string {
  if (sev === 'critical') return 'var(--color-danger)';
  if (sev === 'warning') return 'var(--color-warning)';
  return 'var(--color-text-muted)';
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/** Cost for the Cost column — only meaningful for spend alerts. */
export function alertCost(alert: AlertDoc): number | null {
  const type = String(alert.type ?? '');
  if (type === 'high_cost_call' || type === 'daily_spend_spike') return num(alert.cost_usd);
  if (type === 'daily_global_cost') return num(alert.today_usd);
  return null;
}

/**
 * One-line, type-specific description replacing the Model/Feature columns
 * that were empty for every alert except `high_cost_call`.
 */
export function describeAlert(alert: AlertDoc): string {
  switch (String(alert.type ?? '')) {
    case 'high_cost_call': {
      const parts = [str(alert.model), str(alert.feature)].filter(Boolean);
      const threshold = num(alert.threshold);
      return `${parts.join(' · ') || 'single call'}${threshold != null ? ` · over $${threshold.toFixed(2)}` : ''}`;
    }
    case 'daily_spend_spike': {
      const baseline = num(alert.baseline_daily_usd);
      const mult = num(alert.threshold);
      const fraction = num(alert.day_fraction);
      const bits = [];
      if (baseline != null) bits.push(`baseline $${baseline.toFixed(2)}/day`);
      if (mult != null) bits.push(`${mult}× limit`);
      if (fraction != null) bits.push(`${Math.round(fraction * 24)}h into day`);
      return bits.join(' · ') || 'today\'s spend above baseline';
    }
    case 'daily_global_cost': {
      const threshold = num(alert.threshold_usd);
      return threshold != null ? `global spend today · alarm at $${threshold.toFixed(2)}` : 'global spend today above the daily alarm';
    }
    case 'high_failure_rate': {
      const rate = num(alert.failure_rate);
      const sample = num(alert.sample_size);
      const window = num(alert.window_minutes);
      return `${rate != null ? `${(rate * 100).toFixed(0)}% failed` : 'failure rate high'}${
        sample != null ? ` of ${sample} calls` : ''
      }${window != null ? ` / ${window} min` : ''}`;
    }
    case 'backup_stale': {
      const age = num(alert.age_hours);
      const max = num(alert.max_age_hours);
      if (age == null) return 'no backup found';
      return `last backup ${age.toFixed(1)}h ago${max != null ? ` (max ${max}h)` : ''}`;
    }
    case 'pipeline_stalled': {
      const yt = str(alert.youtube_id);
      const minutes = num(alert.stalled_minutes);
      return `${yt ?? str(alert.video_summary_id) ?? 'video'} idle ${minutes ?? '?'} min → failed`;
    }
    default: {
      const parts = [str(alert.model), str(alert.feature)].filter(Boolean);
      return parts.join(' · ');
    }
  }
}
