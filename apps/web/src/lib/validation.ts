import { z } from "zod";
import { ApiError } from "@/api/client";

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_RE.test(value);
}

// Shared primitives so Login and Register stay in lockstep on email rules.
const email = z
  .string()
  .min(1, "Email is required")
  .regex(EMAIL_RE, "Enter a valid email (e.g. you@example.com)");

export const loginSchema = z.object({
  email,
  password: z.string().min(1, "Password is required"),
});

export const registerSchema = z.object({
  name: z.string().trim().min(1, "Name is required"),
  email,
  password: z
    .string()
    .min(8, "At least 8 characters")
    .regex(/[A-Z]/, "Uppercase letter required")
    .regex(/[a-z]/, "Lowercase letter required")
    .regex(/\d/, "Number required"),
});

export type LoginValues = z.infer<typeof loginSchema>;
export type RegisterValues = z.infer<typeof registerSchema>;

export type FieldErrors<T> = Partial<Record<keyof T, string>>;

/** Flatten a ZodError into a `{ field: firstMessage }` map for inline UI. */
export function fieldErrorsFrom<T>(error: z.ZodError<T>): FieldErrors<T> {
  const out: FieldErrors<T> = {};
  for (const issue of error.issues) {
    const key = issue.path[0] as keyof T | undefined;
    if (key && !(key in out)) out[key] = issue.message;
  }
  return out;
}

/**
 * Translate an auth failure into a user-friendly message using `ApiError`
 * status/code — never substring-match on server strings.
 */
export function translateAuthError(
  err: unknown,
  kind: "login" | "register",
): string {
  if (err instanceof ApiError) {
    if (err.status === 401 || err.code === "INVALID_CREDENTIALS") {
      return "Email or password is incorrect. Please try again.";
    }
    if (err.status === 409 || err.code === "USER_EXISTS") {
      return "An account with this email already exists. Try signing in instead.";
    }
    if (err.status === 429 || err.code === "RATE_LIMITED") {
      return "Too many attempts. Wait a minute and try again.";
    }
    if (err.status === 408 || err.code === "TIMEOUT") {
      return "Couldn't reach the server. Check your connection and try again.";
    }
    if (err.status === 400 || err.status === 422) {
      return kind === "register"
        ? "Please review the highlighted fields and try again."
        : "Please check your email and password.";
    }
    if (err.message) return err.message;
  }
  if (err instanceof TypeError) {
    // fetch() throws TypeError for network failures.
    return "Couldn't reach the server. Check your connection and try again.";
  }
  return kind === "register"
    ? "Registration failed. Please try again."
    : "Sign-in failed. Please try again.";
}

/**
 * Message for a failed demo sign-in. The visitor typed no credentials, so
 * the credential wording of `translateAuthError` would mislead; only rate
 * limiting and connectivity get their own message.
 */
export function translateDemoError(err: unknown): string {
  if (err instanceof ApiError && (err.status === 429 || err.code === "RATE_LIMITED")) {
    return "Too many attempts. Wait a minute and try again.";
  }
  const isTimeout = err instanceof ApiError && (err.status === 408 || err.code === "TIMEOUT");
  if (isTimeout || err instanceof TypeError) {
    return "Couldn't reach the server. Check your connection and try again.";
  }
  return "The demo isn't available right now. Please try again later.";
}
