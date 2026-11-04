import type { UsageSummary } from "@vie/types";
import { request } from "./client";

export type { UsageSummary };

export const usageApi = {
  /** The signed-in user's tokens: balance, plan, the Pro cycle, credits, debt, open holds. */
  me(): Promise<UsageSummary> {
    return request<UsageSummary>("/users/me/usage");
  },
};
