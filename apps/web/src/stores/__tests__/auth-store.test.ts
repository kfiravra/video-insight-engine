import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/mocks/server";
import { createMockUser } from "@/test/mocks/handlers";
import { useAuthStore } from "../auth-store";
import { setAccessToken, getAccessToken } from "@/api/client";

const API_URL = "http://localhost:3000/api";

// Default mock values from handlers
const mockAccessToken = "test-access-token";

describe("authStore", () => {
  beforeEach(() => {
    // Reset store state before each test
    useAuthStore.setState({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: true,
      logoutReason: null,
    });
    // Clear localStorage
    localStorage.clear();
    // Clear access token
    setAccessToken(null);
  });

  describe("initial state", () => {
    it("should have null user and token initially", () => {
      const state = useAuthStore.getState();

      expect(state.user).toBeNull();
      expect(state.accessToken).toBeNull();
      expect(state.isAuthenticated).toBe(false);
      expect(state.isLoading).toBe(true);
      expect(state.logoutReason).toBeNull();
    });
  });

  describe("login", () => {
    it("should set user and token on successful login", async () => {
      const { login } = useAuthStore.getState();

      await login("test@example.com", "password123");

      const state = useAuthStore.getState();
      expect(state.user).toEqual(createMockUser({ email: "test@example.com" }));
      expect(state.accessToken).toBe(mockAccessToken);
      expect(state.isAuthenticated).toBe(true);
    });

    it("should store token in localStorage", async () => {
      const { login } = useAuthStore.getState();

      await login("test@example.com", "password123");

      expect(getAccessToken()).toBe(mockAccessToken);
    });

    it("should throw error on invalid credentials", async () => {
      // Override login to return error
      server.use(
        http.post(`${API_URL}/auth/login`, () => {
          return HttpResponse.json(
            { message: "Invalid credentials" },
            { status: 401 }
          );
        })
      );

      const { login } = useAuthStore.getState();

      await expect(login("wrong@example.com", "wrong")).rejects.toThrow();

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });
  });

  describe("loginDemo", () => {
    it("should authenticate as the demo user", async () => {
      await useAuthStore.getState().loginDemo();

      const state = useAuthStore.getState();
      expect(state).toMatchObject({
        user: { email: "demo@example.com" },
        accessToken: mockAccessToken,
        isAuthenticated: true,
      });
    });

    it("should store the demo token for API requests", async () => {
      await useAuthStore.getState().loginDemo();

      expect(getAccessToken()).toBe(mockAccessToken);
    });

    it("should stay logged out when the demo login is rejected", async () => {
      server.use(
        http.post(`${API_URL}/auth/login`, () =>
          HttpResponse.json(
            { error: "DEMO_DISABLED", message: "The demo is not available", statusCode: 403 },
            { status: 403 }
          )
        )
      );

      await expect(useAuthStore.getState().loginDemo()).rejects.toThrow();
      expect(useAuthStore.getState().isAuthenticated).toBe(false);
    });
  });

  describe("register", () => {
    it("should set user and token on successful registration", async () => {
      const { register } = useAuthStore.getState();

      await register("newuser@example.com", "password123", "New User");

      const state = useAuthStore.getState();
      expect(state.user).toEqual(
        createMockUser({ email: "newuser@example.com", name: "New User" })
      );
      expect(state.accessToken).toBe(mockAccessToken);
      expect(state.isAuthenticated).toBe(true);
    });

    it("should store token in localStorage", async () => {
      const { register } = useAuthStore.getState();

      await register("newuser@example.com", "password123", "New User");

      expect(getAccessToken()).toBe(mockAccessToken);
    });

    it("should throw error when registration fails", async () => {
      // Override registration to return error
      server.use(
        http.post(`${API_URL}/auth/register`, () => {
          return HttpResponse.json(
            { message: "Email already exists" },
            { status: 400 }
          );
        })
      );

      const { register } = useAuthStore.getState();

      await expect(
        register("existing@example.com", "password123", "User")
      ).rejects.toThrow();

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });
  });

  describe("logout", () => {
    it("should clear user and token on logout", async () => {
      // First login
      const { login, logout } = useAuthStore.getState();
      await login("test@example.com", "password123");

      // Then logout
      logout();

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.accessToken).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });

    it("should remove token from localStorage", async () => {
      const { login, logout } = useAuthStore.getState();
      await login("test@example.com", "password123");

      logout();

      expect(getAccessToken()).toBeNull();
    });

    it("should not throw even if logout API fails", async () => {
      // Override logout to fail
      server.use(
        http.post(`${API_URL}/auth/logout`, () => {
          return HttpResponse.json(
            { message: "Server error" },
            { status: 500 }
          );
        })
      );

      const { login, logout } = useAuthStore.getState();
      await login("test@example.com", "password123");

      // Should not throw - fires and forgets
      expect(() => logout()).not.toThrow();

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });
  });

  describe("forceLogout", () => {
    it("should clear user and token with a reason", async () => {
      // First login
      const { login, forceLogout } = useAuthStore.getState();
      await login("test@example.com", "password123");

      // Force logout
      forceLogout("Session expired");

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.accessToken).toBeNull();
      expect(state.isAuthenticated).toBe(false);
      expect(state.logoutReason).toBe("Session expired");
    });

    it("should set null reason when none provided", async () => {
      const { login, forceLogout } = useAuthStore.getState();
      await login("test@example.com", "password123");

      forceLogout();

      const state = useAuthStore.getState();
      expect(state.logoutReason).toBeNull();
    });

    it("should clear token from localStorage", async () => {
      const { login, forceLogout } = useAuthStore.getState();
      await login("test@example.com", "password123");

      forceLogout("Token expired");

      expect(getAccessToken()).toBeNull();
    });
  });

  describe("clearLogoutReason", () => {
    it("should clear the logout reason", () => {
      // Set a logout reason
      useAuthStore.setState({ logoutReason: "Session expired" });

      const { clearLogoutReason } = useAuthStore.getState();
      clearLogoutReason();

      expect(useAuthStore.getState().logoutReason).toBeNull();
    });

    it("should not affect other state", async () => {
      const { login } = useAuthStore.getState();
      await login("test@example.com", "password123");

      // Manually set logout reason
      useAuthStore.setState({ logoutReason: "Test reason" });

      const { clearLogoutReason } = useAuthStore.getState();
      clearLogoutReason();

      const state = useAuthStore.getState();
      expect(state.logoutReason).toBeNull();
      expect(state.user).not.toBeNull();
      expect(state.isAuthenticated).toBe(true);
    });
  });

  describe("checkAuth", () => {
    it("should set isLoading false when no token exists", async () => {
      const { checkAuth } = useAuthStore.getState();

      await checkAuth();

      const state = useAuthStore.getState();
      expect(state.isLoading).toBe(false);
      expect(state.isAuthenticated).toBe(false);
    });

    it("should validate token and fetch user when token exists", async () => {
      // Set a token first
      useAuthStore.setState({ accessToken: mockAccessToken });
      setAccessToken(mockAccessToken);

      const { checkAuth } = useAuthStore.getState();
      await checkAuth();

      const state = useAuthStore.getState();
      expect(state.user).toEqual(createMockUser());
      expect(state.isAuthenticated).toBe(true);
      expect(state.isLoading).toBe(false);
    });

    it("should clear auth state when token is invalid", async () => {
      // Override /auth/me to return 401
      server.use(
        http.get(`${API_URL}/auth/me`, () => {
          return HttpResponse.json(
            { message: "Unauthorized" },
            { status: 401 }
          );
        })
      );

      // Set an invalid token
      useAuthStore.setState({ accessToken: "invalid-token" });
      setAccessToken("invalid-token");

      const { checkAuth } = useAuthStore.getState();
      await checkAuth();

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.accessToken).toBeNull();
      expect(state.isAuthenticated).toBe(false);
      expect(state.isLoading).toBe(false);
    });

    it("should handle server errors gracefully", async () => {
      // Mock server error
      server.use(
        http.get(`${API_URL}/auth/me`, () => {
          return HttpResponse.json(
            { message: "Server error" },
            { status: 500 }
          );
        })
      );

      useAuthStore.setState({ accessToken: mockAccessToken });
      setAccessToken(mockAccessToken);

      const { checkAuth } = useAuthStore.getState();
      await checkAuth();

      const state = useAuthStore.getState();
      expect(state.isAuthenticated).toBe(false);
      expect(state.isLoading).toBe(false);
    });

    it("should clear localStorage token on invalid auth", async () => {
      server.use(
        http.get(`${API_URL}/auth/me`, () => {
          return HttpResponse.json(
            { message: "Unauthorized" },
            { status: 401 }
          );
        })
      );

      useAuthStore.setState({ accessToken: "invalid-token" });
      setAccessToken("invalid-token");

      const { checkAuth } = useAuthStore.getState();
      await checkAuth();

      expect(getAccessToken()).toBeNull();
    });
  });

  describe("setToken", () => {
    it("should update the access token in store and localStorage", () => {
      const { setToken } = useAuthStore.getState();

      setToken("new-token");

      expect(useAuthStore.getState().accessToken).toBe("new-token");
      expect(getAccessToken()).toBe("new-token");
    });

    it("should handle null token", () => {
      // Set a token first
      useAuthStore.setState({ accessToken: "existing-token" });
      setAccessToken("existing-token");

      const { setToken } = useAuthStore.getState();
      setToken(null);

      expect(useAuthStore.getState().accessToken).toBeNull();
      expect(getAccessToken()).toBeNull();
    });

    it("should overwrite existing token", () => {
      const { setToken } = useAuthStore.getState();

      setToken("first-token");
      setToken("second-token");

      expect(useAuthStore.getState().accessToken).toBe("second-token");
      expect(getAccessToken()).toBe("second-token");
    });
  });

  describe("selectors", () => {
    it("useUser selector should return the user from store", async () => {
      const { login } = useAuthStore.getState();
      await login("test@example.com", "password123");

      // Direct selector test (not using hook since we're not in React)
      const user = useAuthStore.getState().user;
      expect(user).toEqual(createMockUser({ email: "test@example.com" }));
    });

    it("useIsAuthenticated selector should return auth state", async () => {
      expect(useAuthStore.getState().isAuthenticated).toBe(false);

      const { login } = useAuthStore.getState();
      await login("test@example.com", "password123");

      expect(useAuthStore.getState().isAuthenticated).toBe(true);
    });

    it("should allow subscribing to specific state slices", async () => {
      // Test that selector pattern works
      const getUser = () => useAuthStore.getState().user;
      const getIsAuth = () => useAuthStore.getState().isAuthenticated;

      expect(getUser()).toBeNull();
      expect(getIsAuth()).toBe(false);

      const { login } = useAuthStore.getState();
      await login("test@example.com", "password123");

      expect(getUser()).not.toBeNull();
      expect(getIsAuth()).toBe(true);
    });
  });

  describe("persistence", () => {
    it("should persist accessToken and user to localStorage", async () => {
      const { login } = useAuthStore.getState();
      await login("test@example.com", "password123");

      // Check that zustand persist stored the data
      const persistedData = localStorage.getItem("vie-auth");
      expect(persistedData).not.toBeNull();

      if (persistedData) {
        const parsed = JSON.parse(persistedData);
        expect(parsed.state.accessToken).toBe(mockAccessToken);
        expect(parsed.state.user).toEqual(
          createMockUser({ email: "test@example.com" })
        );
      }
    });

    it("should only persist partialize fields (accessToken and user)", async () => {
      useAuthStore.setState({
        user: createMockUser(),
        accessToken: mockAccessToken,
        isAuthenticated: true,
        isLoading: false,
        logoutReason: "Test reason",
      });

      // The persisted data should only have partialize fields
      const persistedData = localStorage.getItem("vie-auth");
      if (persistedData) {
        const parsed = JSON.parse(persistedData);
        // Only accessToken and user should be persisted
        expect(parsed.state.accessToken).toBeDefined();
        expect(parsed.state.user).toBeDefined();
        // isLoading and logoutReason should not be in persisted state
        expect(parsed.state.isLoading).toBeUndefined();
        expect(parsed.state.logoutReason).toBeUndefined();
        expect(parsed.state.isAuthenticated).toBeUndefined();
      }
    });
  });
});
