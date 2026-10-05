import { request } from "./client";
import type { AuthResponse, User } from "@/types";

/** Public auth switches for this deployment: self-service signup and demo mode. */
export interface RegistrationStatus {
  open: boolean;
  demoEnabled?: boolean;
}

// API layer - pure HTTP calls, no side effects
// Callers are responsible for token management via setAccessToken
export const authApi = {
  async register(
    email: string,
    password: string,
    name: string
  ): Promise<AuthResponse> {
    return request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    });
  },

  async login(email: string, password: string): Promise<AuthResponse> {
    return request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  /** Signs in the shared demo account; the API holds its credentials. */
  async loginDemo(): Promise<AuthResponse> {
    return request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ demo: true }),
    });
  },

  async logout(): Promise<void> {
    return request("/auth/logout", { method: "POST" });
  },

  async getMe(): Promise<User> {
    return request<User>("/auth/me");
  },

  async getRegistrationStatus(): Promise<RegistrationStatus> {
    return request<RegistrationStatus>("/auth/registration");
  },
};
