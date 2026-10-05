import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../test/mocks/server";
import { authApi } from "../auth";
import { setAccessToken } from "../client";
import { createMockUser } from "../../test/mocks/handlers";

const API_URL = "http://localhost:3000/api";

describe("authApi", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe("register", () => {
    it("should send registration request with email, password, and name", async () => {
      let capturedBody: unknown = null;

      server.use(
        http.post(`${API_URL}/auth/register`, async ({ request }) => {
          capturedBody = await request.json();
          return HttpResponse.json({
            user: createMockUser({
              email: "new@example.com",
              name: "New User",
            }),
            accessToken: "new-token",
          });
        })
      );

      const result = await authApi.register(
        "new@example.com",
        "password123",
        "New User"
      );

      expect(capturedBody).toEqual({
        email: "new@example.com",
        password: "password123",
        name: "New User",
      });
      expect(result.user.email).toBe("new@example.com");
      expect(result.user.name).toBe("New User");
      expect(result.accessToken).toBe("new-token");
    });

    it("should return user and access token on success", async () => {
      server.use(
        http.post(`${API_URL}/auth/register`, () => {
          return HttpResponse.json({
            user: createMockUser({ id: "new-user-id" }),
            accessToken: "fresh-token",
          });
        })
      );

      const result = await authApi.register(
        "test@example.com",
        "password",
        "Test"
      );

      expect(result.user.id).toBe("new-user-id");
      expect(result.accessToken).toBe("fresh-token");
    });

    it("should throw on registration failure", async () => {
      server.use(
        http.post(`${API_URL}/auth/register`, () => {
          return HttpResponse.json(
            { message: "Email already exists", error: "EMAIL_EXISTS" },
            { status: 409 }
          );
        })
      );

      await expect(
        authApi.register("existing@example.com", "password", "Test")
      ).rejects.toThrow("Email already exists");
    });

    it("should handle validation errors", async () => {
      server.use(
        http.post(`${API_URL}/auth/register`, () => {
          return HttpResponse.json(
            { message: "Invalid email format", error: "VALIDATION_ERROR" },
            { status: 400 }
          );
        })
      );

      await expect(
        authApi.register("invalid-email", "password", "Test")
      ).rejects.toThrow("Invalid email format");
    });
  });

  describe("login", () => {
    it("should send login request with email and password", async () => {
      let capturedBody: unknown = null;

      server.use(
        http.post(`${API_URL}/auth/login`, async ({ request }) => {
          capturedBody = await request.json();
          return HttpResponse.json({
            user: createMockUser({ email: "user@example.com" }),
            accessToken: "login-token",
          });
        })
      );

      await authApi.login("user@example.com", "mypassword");

      expect(capturedBody).toEqual({
        email: "user@example.com",
        password: "mypassword",
      });
    });

    it("should return user and access token on success", async () => {
      server.use(
        http.post(`${API_URL}/auth/login`, () => {
          return HttpResponse.json({
            user: createMockUser({
              id: "logged-in-user",
              email: "user@example.com",
              name: "Logged In User",
            }),
            accessToken: "session-token",
          });
        })
      );

      const result = await authApi.login("user@example.com", "password");

      expect(result.user.id).toBe("logged-in-user");
      expect(result.user.email).toBe("user@example.com");
      expect(result.user.name).toBe("Logged In User");
      expect(result.accessToken).toBe("session-token");
    });

    it("should throw on invalid credentials", async () => {
      server.use(
        http.post(`${API_URL}/auth/login`, () => {
          return HttpResponse.json(
            { message: "Invalid email or password", error: "INVALID_CREDENTIALS" },
            { status: 401 }
          );
        })
      );

      await expect(
        authApi.login("user@example.com", "wrongpassword")
      ).rejects.toThrow("Invalid email or password");
    });

    it("should throw on account locked", async () => {
      server.use(
        http.post(`${API_URL}/auth/login`, () => {
          return HttpResponse.json(
            { message: "Account is locked", error: "ACCOUNT_LOCKED" },
            { status: 403 }
          );
        })
      );

      await expect(
        authApi.login("locked@example.com", "password")
      ).rejects.toThrow("Account is locked");
    });
  });

  describe("loginDemo", () => {
    it("should send only the demo flag, never credentials", async () => {
      let capturedBody: unknown = null;
      server.use(
        http.post(`${API_URL}/auth/login`, async ({ request }) => {
          capturedBody = await request.json();
          return HttpResponse.json({
            user: createMockUser({ email: "demo@example.com" }),
            accessToken: "demo-token",
          });
        })
      );

      await authApi.loginDemo();

      expect(capturedBody).toEqual({ demo: true });
    });

    it("should return the demo user and access token on success", async () => {
      const result = await authApi.loginDemo();

      expect(result).toMatchObject({
        user: { email: "demo@example.com" },
        accessToken: "test-access-token",
      });
    });

    it("should throw DEMO_DISABLED when the deployment has no demo account", async () => {
      server.use(
        http.post(`${API_URL}/auth/login`, () =>
          HttpResponse.json(
            { error: "DEMO_DISABLED", message: "The demo is not available", statusCode: 403 },
            { status: 403 }
          )
        )
      );

      await expect(authApi.loginDemo()).rejects.toMatchObject({ status: 403, code: "DEMO_DISABLED" });
    });
  });

  describe("logout", () => {
    it("should send logout request", async () => {
      let logoutCalled = false;

      server.use(
        http.post(`${API_URL}/auth/logout`, () => {
          logoutCalled = true;
          return new HttpResponse(null, { status: 204 });
        })
      );

      setAccessToken("valid-token");

      await authApi.logout();

      expect(logoutCalled).toBe(true);
    });

    it("should complete without error on 204 response", async () => {
      server.use(
        http.post(`${API_URL}/auth/logout`, () => {
          return new HttpResponse(null, { status: 204 });
        })
      );

      setAccessToken("valid-token");

      await expect(authApi.logout()).resolves.toBeUndefined();
    });

    it("should throw on logout failure", async () => {
      server.use(
        http.post(`${API_URL}/auth/logout`, () => {
          return HttpResponse.json(
            { message: "Server error" },
            { status: 500 }
          );
        })
      );

      setAccessToken("valid-token");

      await expect(authApi.logout()).rejects.toThrow();
    });
  });

  describe("getMe", () => {
    it("should return current user when authenticated", async () => {
      server.use(
        http.get(`${API_URL}/auth/me`, () => {
          return HttpResponse.json(
            createMockUser({
              id: "current-user",
              email: "me@example.com",
              name: "Current User",
            })
          );
        })
      );

      setAccessToken("valid-token");

      const user = await authApi.getMe();

      expect(user.id).toBe("current-user");
      expect(user.email).toBe("me@example.com");
      expect(user.name).toBe("Current User");
    });

    it("should throw on unauthorized access", async () => {
      server.use(
        http.get(`${API_URL}/auth/me`, () => {
          return HttpResponse.json(
            { message: "Unauthorized", error: "UNAUTHORIZED" },
            { status: 401 }
          );
        })
      );

      await expect(authApi.getMe()).rejects.toThrow("Unauthorized");
    });

    it("should include authorization header in request", async () => {
      let capturedAuth: string | null = null;

      server.use(
        http.get(`${API_URL}/auth/me`, ({ request }) => {
          capturedAuth = request.headers.get("Authorization");
          return HttpResponse.json(createMockUser());
        })
      );

      setAccessToken("my-token");

      await authApi.getMe();

      expect(capturedAuth).toBe("Bearer my-token");
    });
  });
});
