import { create } from "zustand";
import { persist } from "zustand/middleware";
import { authApi } from "@/api/auth";
import { getSessionEpoch, isSessionCurrent, markSessionChange, setAccessToken } from "@/api/client";
import { resetClientSession } from "@/lib/session-reset";
import type { AuthResponse, User } from "@/types";

/** The sign-out call still in flight, so a login that follows can wait for
 *  the server to clear the old refresh cookie before it sets the new one. */
let pendingLogout: Promise<void> | null = null;

export function pendingLogoutRequest(): Promise<void> | null {
  return pendingLogout;
}

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
    (set, get) => {
      /**
       * Sign in and wait for the session to be real before anyone navigates:
       * the token is stored, then `GET /auth/me` must answer for it. The
       * store flips to authenticated only on that answer, so a protected
       * route never renders on a token the api has not yet vouched for.
       */
      const establishSession = async (
        authenticate: () => Promise<AuthResponse>,
      ): Promise<void> => {
        // A sign-out still in flight clears the refresh cookie when it lands;
        // let it land before this login sets its own.
        await pendingLogout;
        const { accessToken } = await authenticate();
        // Nothing cached from an earlier session may be served to this one.
        resetClientSession();
        markSessionChange();
        setAccessToken(accessToken);
        set({ accessToken });
        try {
          const user = await authApi.getMe();
          set({
            user,
            accessToken,
            isAuthenticated: true,
            isLoading: false,
            anonymousOutputCount: 0,
          });
        } catch (err) {
          if (get().accessToken === accessToken) {
            setAccessToken(null);
            set({ user: null, accessToken: null, isAuthenticated: false, isLoading: false });
          }
          throw err;
        }
      };

      return {
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: true,
      logoutReason: null,
      anonymousOutputCount: 0,
      login: (email, password) =>
        establishSession(() => authApi.login(email, password)),

      loginDemo: () => establishSession(() => authApi.loginDemo()),

      register: (email, password, name) =>
        establishSession(() => authApi.register(email, password, name)),

      logout: () => {
        // The server call clears the refresh cookie; the client does not wait
        // for it, but the next login does (see pendingLogoutRequest).
        const call = authApi.logout().catch(() => {});
        pendingLogout = call;
        void call.finally(() => {
          if (pendingLogout === call) pendingLogout = null;
        });
        setAccessToken(null);
        markSessionChange();
        // Nothing of this user survives for the next one: query cache,
        // transcript, folder state, open streams.
        resetClientSession();
        set({ user: null, accessToken: null, isAuthenticated: false });
      },

      forceLogout: (reason?: string) => {
        setAccessToken(null);
        markSessionChange();
        resetClientSession();
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

        // A sign-in or sign-out that lands while this check is in flight
        // owns the store; the check's answer is about a session that is gone.
        const epoch = getSessionEpoch();
        try {
          setAccessToken(accessToken);
          const user = await authApi.getMe();
          if (!isSessionCurrent(epoch)) return;
          set({ user, isAuthenticated: true, isLoading: false });
        } catch {
          if (!isSessionCurrent(epoch)) return;
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
      };
    },
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
