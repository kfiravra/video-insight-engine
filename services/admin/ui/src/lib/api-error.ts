export class ApiError extends Error {
  status: number;
  /** The server's own explanation (vie-api's validation message, FastAPI's 422 text), when it sent one. */
  detail?: string;
  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** What to show an operator for a failed call: the server's explanation, else the generic message. */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  return error instanceof Error ? error.message : 'Something went wrong';
}

interface FastApiValidationIssue {
  msg?: unknown;
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** FastAPI's 422 body: a list of issues, each with a `msg`. */
function joinIssues(issues: FastApiValidationIssue[]): string | undefined {
  const messages = issues.map((issue) => issue.msg).filter((m): m is string => typeof m === 'string');
  return messages.length > 0 ? messages.join('; ') : undefined;
}

/**
 * The human-readable part of an error body: a string `detail`, vie-api's
 * `{error, message}` envelope passed through as `detail`, FastAPI's list of 422
 * issues, or the top-level `{error}` that vie-admin's own auth middleware
 * answers with (it never goes through FastAPI's `detail`). undefined when the
 * body is not JSON or carries nothing usable.
 */
export async function errorDetail(res: Response): Promise<string | undefined> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return undefined; // an empty or non-JSON error body has nothing to show
  }
  const { detail, error } = (body ?? {}) as { detail?: unknown; error?: unknown };
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return joinIssues(detail);
  const envelope = (detail ?? {}) as { message?: unknown };
  return asString(envelope.message) ?? asString(error);
}

/**
 * True when vie-admin or vie-api looked at the request and refused it (a 4xx):
 * nothing was applied. Anything else, a 5xx, a timeout or a dropped connection,
 * can have come after the change was made.
 */
export function wasRefused(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}
