import { describe, it, expect, vi, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../test/mocks/server";
import {
  request,
  getAccessToken,
  setAccessToken,
  refreshToken,
  ApiError,
} from "../client";
import { useAuthStore } from "@/stores/auth-store";

const API_URL = "http://localhost:3000/api";

describe("API Client", () => {
  beforeEach(() => {
    // Clear localStorage and auth store before each test
    localStorage.clear();
    useAuthStore.setState({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: false,
      logoutReason: null,
    });
  });

  describe("setAccessToken", () => {
    it("should store token in localStorage when provided", () => {
      setAccessToken("test-token");

      expect(localStorage.getItem("accessToken")).toBe("test-token");
    });

    it("should remove token from localStorage when null", () => {
      localStorage.setItem("accessToken", "existing-token");

      setAccessToken(null);

      expect(localStorage.getItem("accessToken")).toBeNull();
    });
  });

  describe("getAccessToken", () => {
    it("should return token from localStorage", () => {
      localStorage.setItem("accessToken", "stored-token");

      const token = getAccessToken();

      expect(token).toBe("stored-token");
    });

    it("should return null when no token exists", () => {
      const token = getAccessToken();

      expect(token).toBeNull();
    });
  });

  describe("refreshToken", () => {
    it("should return true and update token on successful refresh", async () => {
      server.use(
        http.post(`${API_URL}/auth/refresh`, () => {
          return HttpResponse.json({ accessToken: "new-access-token" });
        })
      );

      const result = await refreshToken();

      expect(result).toBe(true);
      expect(localStorage.getItem("accessToken")).toBe("new-access-token");
    });

    it("should return false on refresh failure", async () => {
      server.use(
        http.post(`${API_URL}/auth/refresh`, () => {
          return new HttpResponse(null, { status: 401 });
        })
      );

      const result = await refreshToken();

      expect(result).toBe(false);
    });

    it("should return false on network error", async () => {
      server.use(
        http.post(`${API_URL}/auth/refresh`, () => {
          return HttpResponse.error();
        })
      );

      const result = await refreshToken();

      expect(result).toBe(false);
    });

    it("should update Zustand store with new token", async () => {
      server.use(
        http.post(`${API_URL}/auth/refresh`, () => {
          return HttpResponse.json({ accessToken: "refreshed-token" });
        })
      );

      await refreshToken();

      expect(useAuthStore.getState().accessToken).toBe("refreshed-token");
    });
  });

  describe("request", () => {
    describe("headers and auth", () => {
      it("should include Authorization header when token exists", async () => {
        let capturedHeaders: Headers | null = null;

        server.use(
          http.get(`${API_URL}/test`, ({ request }) => {
            capturedHeaders = request.headers;
            return HttpResponse.json({ success: true });
          })
        );

        setAccessToken("test-bearer-token");

        await request("/test");

        expect(capturedHeaders?.get("Authorization")).toBe(
          "Bearer test-bearer-token"
        );
      });

      it("should not include Authorization header when no token", async () => {
        let capturedHeaders: Headers | null = null;

        server.use(
          http.get(`${API_URL}/test`, ({ request }) => {
            capturedHeaders = request.headers;
            return HttpResponse.json({ success: true });
          })
        );

        await request("/test");

        expect(capturedHeaders?.get("Authorization")).toBeNull();
      });

      it("should send the JSON Content-Type with a body", async () => {
        let capturedHeaders: Headers | null = null;

        server.use(
          http.post(`${API_URL}/test`, ({ request }) => {
            capturedHeaders = request.headers;
            return HttpResponse.json({ success: true });
          })
        );

        await request("/test", { method: "POST", body: JSON.stringify({ a: 1 }) });

        expect(capturedHeaders?.get("Content-Type")).toBe("application/json");
      });

      it("should not claim a JSON body on a bodiless request", async () => {
        // A proxy that adds Content-Length: 0 turns a bodiless DELETE with a
        // JSON content-type into an "empty JSON body" the api rejects (400).
        const captured: Record<string, string | null> = {};

        server.use(
          http.get(`${API_URL}/test`, ({ request }) => {
            captured.get = request.headers.get("Content-Type");
            return HttpResponse.json({ success: true });
          }),
          http.delete(`${API_URL}/test`, ({ request }) => {
            captured.delete = request.headers.get("Content-Type");
            return HttpResponse.json({ success: true });
          })
        );

        await request("/test");
        await request("/test", { method: "DELETE" });

        expect(captured.get).toBeNull();
        expect(captured.delete).toBeNull();
      });

      it("should allow custom headers", async () => {
        let capturedHeaders: Headers | null = null;

        server.use(
          http.get(`${API_URL}/test`, ({ request }) => {
            capturedHeaders = request.headers;
            return HttpResponse.json({ success: true });
          })
        );

        await request("/test", {
          headers: { "X-Custom-Header": "custom-value" },
        });

        expect(capturedHeaders?.get("X-Custom-Header")).toBe("custom-value");
      });
    });

    describe("response handling", () => {
      it("should return JSON response data", async () => {
        server.use(
          http.get(`${API_URL}/test`, () => {
            return HttpResponse.json({
              data: { id: 1, name: "Test" },
            });
          })
        );

        const result = await request<{ data: { id: number; name: string } }>(
          "/test"
        );

        expect(result.data).toEqual({ id: 1, name: "Test" });
      });

      it("should return undefined for 204 No Content responses", async () => {
        server.use(
          http.delete(`${API_URL}/test`, () => {
            return new HttpResponse(null, { status: 204 });
          })
        );

        const result = await request("/test", { method: "DELETE" });

        expect(result).toBeUndefined();
      });

      it("should handle POST requests with body", async () => {
        let capturedBody: unknown = null;

        server.use(
          http.post(`${API_URL}/test`, async ({ request }) => {
            capturedBody = await request.json();
            return HttpResponse.json({ success: true });
          })
        );

        await request("/test", {
          method: "POST",
          body: JSON.stringify({ name: "Test", value: 123 }),
        });

        expect(capturedBody).toEqual({ name: "Test", value: 123 });
      });
    });

    describe("error handling", () => {
      it("should throw ApiError with status and message on error response", async () => {
        server.use(
          http.get(`${API_URL}/test`, () => {
            return HttpResponse.json(
              { message: "Not found", error: "NOT_FOUND" },
              { status: 404 }
            );
          })
        );

        await expect(request("/test")).rejects.toThrow(ApiError);

        try {
          await request("/test");
        } catch (error) {
          expect(error).toBeInstanceOf(ApiError);
          expect((error as ApiError).status).toBe(404);
          expect((error as ApiError).message).toBe("Not found");
          expect((error as ApiError).code).toBe("NOT_FOUND");
        }
      });

      it("should handle error response without JSON body", async () => {
        server.use(
          http.get(`${API_URL}/test`, () => {
            return new HttpResponse("Server Error", { status: 500 });
          })
        );

        try {
          await request("/test");
        } catch (error) {
          expect(error).toBeInstanceOf(ApiError);
          expect((error as ApiError).status).toBe(500);
          expect((error as ApiError).message).toBe("Request failed");
        }
      });

      it("should throw ApiError with TIMEOUT code on request timeout", async () => {
        // Note: This test relies on the REQUEST_TIMEOUT_MS constant being relatively short
        // In real scenarios, you might want to mock timers
        server.use(
          http.get(`${API_URL}/test`, async () => {
            // Simulate a very long delay
            await new Promise((resolve) => setTimeout(resolve, 100000));
            return HttpResponse.json({ success: true });
          })
        );

        // Create an abort controller to simulate timeout
        const controller = new AbortController();

        // Abort immediately to simulate timeout behavior
        setTimeout(() => controller.abort(), 10);

        try {
          await request("/test", { signal: controller.signal });
        } catch (error) {
          expect(error).toBeInstanceOf(ApiError);
          expect((error as ApiError).code).toBe("TIMEOUT");
          expect((error as ApiError).status).toBe(408);
        }
      });
    });

    describe("401 handling and token refresh", () => {
      it("should attempt token refresh on 401 response", async () => {
        let refreshCalled = false;
        let requestCount = 0;

        server.use(
          http.get(`${API_URL}/test`, () => {
            requestCount++;
            if (requestCount === 1) {
              return new HttpResponse(null, { status: 401 });
            }
            return HttpResponse.json({ success: true });
          }),
          http.post(`${API_URL}/auth/refresh`, () => {
            refreshCalled = true;
            return HttpResponse.json({ accessToken: "new-token" });
          })
        );

        setAccessToken("old-token");

        const result = await request<{ success: boolean }>("/test");

        expect(refreshCalled).toBe(true);
        expect(requestCount).toBe(2);
        expect(result.success).toBe(true);
      });

      it("should force logout when refresh fails", async () => {
        const forceLogoutSpy = vi.fn();
        useAuthStore.setState({ forceLogout: forceLogoutSpy });

        server.use(
          http.get(`${API_URL}/test`, () => {
            return new HttpResponse(null, { status: 401 });
          }),
          http.post(`${API_URL}/auth/refresh`, () => {
            return new HttpResponse(null, { status: 401 });
          })
        );

        setAccessToken("expired-token");

        await expect(request("/test")).rejects.toThrow(ApiError);

        expect(forceLogoutSpy).toHaveBeenCalledWith(
          "Session expired. Please log in again."
        );
      });

      it("should not attempt refresh on 401 without existing token", async () => {
        let refreshCalled = false;

        server.use(
          http.get(`${API_URL}/test`, () => {
            return HttpResponse.json(
              { message: "Unauthorized" },
              { status: 401 }
            );
          }),
          http.post(`${API_URL}/auth/refresh`, () => {
            refreshCalled = true;
            return HttpResponse.json({ accessToken: "new-token" });
          })
        );

        // No token set
        await expect(request("/test")).rejects.toThrow(ApiError);

        expect(refreshCalled).toBe(false);
      });

      it("should not retry more than once to prevent infinite loop", async () => {
        let requestCount = 0;

        server.use(
          http.get(`${API_URL}/test`, () => {
            requestCount++;
            return new HttpResponse(null, { status: 401 });
          }),
          http.post(`${API_URL}/auth/refresh`, () => {
            return HttpResponse.json({ accessToken: "new-token" });
          })
        );

        setAccessToken("token");

        await expect(request("/test")).rejects.toThrow();

        // Should be called at most twice (initial + one retry after refresh)
        expect(requestCount).toBeLessThanOrEqual(2);
      });
    });
  });

  describe("ApiError", () => {
    it("should create error with correct properties", () => {
      const error = new ApiError(404, "Not found", "NOT_FOUND");

      expect(error.status).toBe(404);
      expect(error.message).toBe("Not found");
      expect(error.code).toBe("NOT_FOUND");
      expect(error.name).toBe("ApiError");
    });

    it("should be instanceof Error", () => {
      const error = new ApiError(500, "Server error");

      expect(error).toBeInstanceOf(Error);
      expect(error).toBeInstanceOf(ApiError);
    });

    it("should work without error code", () => {
      const error = new ApiError(400, "Bad request");

      expect(error.code).toBeUndefined();
    });
  });
});
