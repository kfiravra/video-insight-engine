// What changed between the `before` and `after` snapshots of an audit row, as
// one line per changed field. Snapshots are small plain-JSON objects (an account
// snapshot, a purge summary); nested objects are walked into dotted paths and
// arrays are compared and shown whole.

export interface DiffLine {
  path: string;
  /** null = the field did not exist before. */
  before: string | null;
  /** null = the field no longer exists. */
  after: string | null;
}

/** Bookkeeping that changes on every write, so it says nothing about the action. */
const IGNORED_TOP_LEVEL = new Set(['updatedAt', 'version']);

const MAX_VALUE_LENGTH = 120;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function show(value: unknown): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text.length > MAX_VALUE_LENGTH ? `${text.slice(0, MAX_VALUE_LENGTH)}…` : text;
}

/** Every leaf of `value` keyed by dotted path. A non-object snapshot is one leaf named "(value)". */
function flatten(value: unknown, prefix = ''): Map<string, unknown> {
  const leaves = new Map<string, unknown>();
  if (!isRecord(value)) {
    if (value !== undefined) leaves.set(prefix || '(value)', value);
    return leaves;
  }
  for (const [key, child] of Object.entries(value)) {
    if (!prefix && IGNORED_TOP_LEVEL.has(key)) continue;
    const path = prefix ? `${prefix}.${key}` : key;
    // An empty object has no fields to compare, so it adds no line (`{}` and absent read the same).
    if (isRecord(child)) {
      for (const [leafPath, leaf] of flatten(child, path)) leaves.set(leafPath, leaf);
    } else {
      leaves.set(path, child);
    }
  }
  return leaves;
}

/** The changed fields between two snapshots, sorted by path; empty when nothing differs. */
export function diffAudit(before: unknown, after: unknown): DiffLine[] {
  const was = flatten(before);
  const now = flatten(after);
  const paths = [...new Set([...was.keys(), ...now.keys()])].sort();

  return paths.flatMap((path) => {
    const hadBefore = was.has(path);
    const hasAfter = now.has(path);
    if (hadBefore && hasAfter && JSON.stringify(was.get(path)) === JSON.stringify(now.get(path))) return [];
    return [
      {
        path,
        before: hadBefore ? show(was.get(path)) : null,
        after: hasAfter ? show(now.get(path)) : null,
      },
    ];
  });
}
