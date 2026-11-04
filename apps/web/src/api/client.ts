import { useAuthStore } from "@/stores/auth-store";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3000/api";
const REQUEST_TIMEOUT_MS = 30000;

class ApiError extends Error {
  status: number;
  code?: string;
  /** The error body: extra fields such as needed / available on a 402. */
  details?: Record<string, unknown>;

  constructor(
    status: number,
    message: string,
    code?: string,
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

// Single source of truth for token - localStorage only (no in-memory duplicate)
export function setAccessToken(token: string | null): void {
  if (token) {
    localStorage.setItem("accessToken", token);
  } else {
    localStorage.removeItem("accessToken");
  }
}

export function getAccessToken(): string | null {
  return localStorage.getItem("accessToken");
}

// The session epoch changes whenever the signed-in identity does (sign-in,
// sign-out). A request captures it when it starts; a 401 that lands after
// the epoch moved belongs to a session that no longer exists and must never
// refresh or end the session that replaced it.
let sessionEpoch = 0;

/** Called by the auth store on every sign-in and sign-out. */
export function markSessionChange(): void {
  sessionEpoch += 1;
}

export function getSessionEpoch(): number {
  return sessionEpoch;
}

/** True when the session a request started under is still the live one. */
export function isSessionCurrent(epoch: number): boolean {
  return epoch === sessionEpoch;
}

let refreshInFlight: Promise<boolean> | null = null;

async function requestNewAccessToken(): Promise<boolean> {
  const epoch = sessionEpoch;
  try {
    const res = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });

    if (!res.ok) return false;

    const data = await res.json();
    // The session changed while the refresh was in flight: this token was
    // minted for a session that is gone, so leave the live one alone.
    if (!isSessionCurrent(epoch)) return false;
    setAccessToken(data.accessToken);
    // Also update Zustand store to sync streaming hooks
    useAuthStore.getState().setToken(data.accessToken);
    return true;
  } catch {
    return false;
  }
}

/**
 * Exchange the refresh cookie for a new access token. Single-flight: every
 * 401 that lands while a refresh is in flight shares it, so a burst of
 * expired requests costs one call against the refresh rate limit.
 */
export function refreshToken(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = requestNewAccessToken().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

export async function request<T>(
  endpoint: string,
  options?: RequestInit & { signal?: AbortSignal },
  _isRetry = false
): Promise<T> {
  const token = getAccessToken();
  const epoch = getSessionEpoch();

  // Create timeout controller if no external signal provided
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const signal = options?.signal || controller.signal;

  try {
    const res = await fetch(`${API_URL}${endpoint}`, {
      ...options,
      signal,
      headers: {
        // Only a request that carries a body claims a JSON body: a bodiless
        // DELETE/POST sent with this header is rejected by the api as an
        // empty JSON body once a proxy in between adds `Content-Length: 0`.
        ...(options?.body != null && { "Content-Type": "application/json" }),
        ...(token && { Authorization: `Bearer ${token}` }),
        ...options?.headers,
      },
      credentials: "include",
    });

    clearTimeout(timeoutId);

    // Handle 401 - try refresh (only once to prevent infinite recursion)
    if (res.status === 401 && token && !_isRetry) {
      if (!isSessionCurrent(epoch)) {
        // Sent under a session that has since signed out or been replaced:
        // the reply says nothing about the live session.
        throw new ApiError(401, "Session ended", "SESSION_ENDED");
      }
      // A sibling request may already have refreshed; otherwise do it here.
      const refreshed = getAccessToken() !== token || (await refreshToken());
      if (refreshed) {
        return request(endpoint, options, true);
      }
      // Refresh failed - end the session, unless it already changed hands
      if (isSessionCurrent(epoch)) {
        useAuthStore.getState().forceLogout("Session expired. Please log in again.");
      }
      throw new ApiError(401, "Session expired", "SESSION_EXPIRED");
    }

    if (!res.ok) {
      const error = await res
        .json()
        .catch(() => ({ message: "Request failed" }));
      throw new ApiError(res.status, error.message, error.error, error);
    }

    if (res.status === 204) return undefined as T;
    return res.json();
  } catch (error) {
    clearTimeout(timeoutId);
    // Handle AbortError (may be DOMException or Error depending on environment)
    if (
      error &&
      typeof error === "object" &&
      "name" in error &&
      (error as { name: string }).name === "AbortError"
    ) {
      throw new ApiError(408, "Request timeout", "TIMEOUT");
    }
    throw error;
  }
}

export { ApiError };
