import { create } from "zustand";
import { persist } from "zustand/middleware";
import { authApi } from "@/api/auth";
import { setAccessToken } from "@/api/client";
import { useChatStore } from "./chat-store";
import type { User } from "@/types";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  logoutReason: string | null;
  anonymousOutputCount: number;
  // Actions
  login: (email: string, password: string) => Promise<void>;
  loginDemo: () => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  logout: () => void;
  forceLogout: (reason?: string) => void;
  clearLogoutReason: () => void;
  checkAuth: () => Promise<void>;
  setToken: (token: string | null) => void;
  incrementAnonymousCount: () => void;
  resetAnonymousCount: () => void;
  shouldShowAuthPrompt: () => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: true,
      logoutReason: null,
      anonymousOutputCount: 0,
      login: async (email, password) => {
        const { user, accessToken } = await authApi.login(email, password);
        setAccessToken(accessToken);
        set({ user, accessToken, isAuthenticated: true, anonymousOutputCount: 0 });
      },

      loginDemo: async () => {
        const { user, accessToken } = await authApi.loginDemo();
        setAccessToken(accessToken);
        set({ user, accessToken, isAuthenticated: true, anonymousOutputCount: 0 });
      },

      register: async (email, password, name) => {
        const { user, accessToken } = await authApi.register(
          email,
          password,
          name
        );
        setAccessToken(accessToken);
        set({ user, accessToken, isAuthenticated: true, anonymousOutputCount: 0 });
      },

      logout: () => {
        authApi.logout().catch(() => {}); // Fire and forget
        setAccessToken(null);
        // Don't leak one user's chat transcript to the next session.
        useChatStore.getState().clearMessages();
        set({ user: null, accessToken: null, isAuthenticated: false });
      },

      forceLogout: (reason?: string) => {
        setAccessToken(null);
        // Don't leak one user's chat transcript to the next session.
        useChatStore.getState().clearMessages();
        set({
          user: null,
          accessToken: null,
          isAuthenticated: false,
          logoutReason: reason || null,
        });
      },

      clearLogoutReason: () => {
        set({ logoutReason: null });
      },

      checkAuth: async () => {
        const { accessToken } = get();
        if (!accessToken) {
          set({ isLoading: false, isAuthenticated: false });
          return;
        }

        try {
          setAccessToken(accessToken);
          const user = await authApi.getMe();
          set({ user, isAuthenticated: true, isLoading: false });
        } catch {
          setAccessToken(null);
          set({
            user: null,
            accessToken: null,
            isAuthenticated: false,
            isLoading: false,
          });
        }
      },

      setToken: (token) => {
        setAccessToken(token);
        set({ accessToken: token });
      },

      incrementAnonymousCount: () => {
        set((state) => ({ anonymousOutputCount: state.anonymousOutputCount + 1 }));
      },

      resetAnonymousCount: () => {
        set({ anonymousOutputCount: 0 });
      },

      shouldShowAuthPrompt: () => {
        return get().anonymousOutputCount >= 3;
      },

    }),
    {
      name: "vie-auth",
      partialize: (state) => ({
        accessToken: state.accessToken,
        user: state.user,
        anonymousOutputCount: state.anonymousOutputCount,
      }),
    }
  )
);

// Selectors (prevent unnecessary re-renders)
export const useUser = () => useAuthStore((s) => s.user);
export const useIsAuthenticated = () => useAuthStore((s) => s.isAuthenticated);
