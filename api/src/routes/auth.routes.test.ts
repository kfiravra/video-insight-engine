import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { config } from '../config.js';
import { buildTestApp, createMockContainer, getAuthHeader, type MockContainer } from '../test/helpers.js';

describe('auth routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let authHeader: string;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    authHeader = await getAuthHeader(app);
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('POST /api/auth/register', () => {
    it('should register a new user', async () => {
      const mockUser = { id: 'u1', email: 'new@example.com' };
      mockContainer.authService.register.mockResolvedValue(mockUser);

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/register',
        headers: { 'content-type': 'application/json' },
        payload: {
          email: 'new@example.com',
          password: 'SecurePass123',  // Must have uppercase, lowercase, number
          name: 'Test User',
        },
      });

      expect(response.statusCode).toBe(201);
      expect(mockContainer.authService.register).toHaveBeenCalledWith({
        email: 'new@example.com',
        password: 'SecurePass123',
        name: 'Test User',
      });
      const body = response.json();
      expect(body).toHaveProperty('accessToken');
      expect(body).toHaveProperty('expiresIn');
      expect(body).toHaveProperty('user', mockUser);
    });

    it('should return 400 for invalid email', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/register',
        headers: { 'content-type': 'application/json' },
        payload: {
          email: 'invalid-email',
          password: 'SecurePass123',
          name: 'Test',
        },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should return 400 for short password', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/register',
        headers: { 'content-type': 'application/json' },
        payload: {
          email: 'test@example.com',
          password: '123',
          name: 'Test',
        },
      });

      expect(response.statusCode).toBe(400);
    });

    it('should return 409 when email already exists', async () => {
      const { EmailExistsError } = await import('../utils/errors.js');
      mockContainer.authService.register.mockRejectedValue(new EmailExistsError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/register',
        headers: { 'content-type': 'application/json' },
        payload: {
          email: 'existing@example.com',
          password: 'SecurePass123',
          name: 'Test User',
        },
      });

      expect(response.statusCode).toBe(409);
    });
  });

  describe('POST /api/auth/login', () => {
    it('should login an existing user', async () => {
      const mockUser = { id: 'u1', email: 'user@example.com' };
      mockContainer.authService.login.mockResolvedValue(mockUser);

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/login',
        headers: { 'content-type': 'application/json' },
        payload: {
          email: 'user@example.com',
          password: 'correctPassword',
        },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.authService.login).toHaveBeenCalledWith({
        email: 'user@example.com',
        password: 'correctPassword',
      });
      const body = response.json();
      expect(body).toHaveProperty('accessToken');
      expect(body).toHaveProperty('expiresIn');
      expect(body).toHaveProperty('user', mockUser);
    });

    it('should return 401 for invalid credentials', async () => {
      const { InvalidCredentialsError } = await import('../utils/errors.js');
      mockContainer.authService.login.mockRejectedValue(new InvalidCredentialsError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/login',
        headers: { 'content-type': 'application/json' },
        payload: {
          email: 'user@example.com',
          password: 'wrongPassword',
        },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 400 for missing fields', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/login',
        headers: { 'content-type': 'application/json' },
        payload: {
          email: 'user@example.com',
          // missing password
        },
      });

      expect(response.statusCode).toBe(400);
    });
  });

  describe('POST /api/auth/refresh', () => {
    it('should return 401 REFRESH_EXPIRED when no refresh token cookie', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/refresh',
      });

      expect(response.statusCode).toBe(401);
      // Documented envelope (docs/ERROR-HANDLING.md): REFRESH_EXPIRED tells
      // the FE the session is gone — re-login, don't retry.
      expect(response.json()).toEqual({
        error: 'REFRESH_EXPIRED',
        message: 'Session expired, please login',
        statusCode: 401,
      });
    });

    it('should issue a new access token when a valid refresh cookie is presented', async () => {
      const mockUser = { id: 'u1', email: 'user@example.com' };
      mockContainer.authService.login.mockResolvedValue(mockUser);

      const loginResponse = await app.inject({
        method: 'POST',
        url: '/api/auth/login',
        headers: { 'content-type': 'application/json' },
        payload: { email: 'user@example.com', password: 'correctPassword' },
      });
      const setCookie = loginResponse.headers['set-cookie'];
      const cookieLine = Array.isArray(setCookie) ? setCookie[0] : setCookie;
      const refreshToken = cookieLine?.match(/refreshToken=([^;]+)/)?.[1];
      expect(refreshToken).toBeDefined();

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/refresh',
        headers: { cookie: `refreshToken=${refreshToken}` },
      });

      expect(response.statusCode).toBe(200);
      const body = response.json();
      expect(body).toHaveProperty('accessToken');
      expect(body).toHaveProperty('expiresIn');

      // The freshly minted access token must authenticate protected routes
      mockContainer.authService.getUser.mockResolvedValue(mockUser);
      const meResponse = await app.inject({
        method: 'GET',
        url: '/api/auth/me',
        headers: { authorization: `Bearer ${body.accessToken}` },
      });
      expect(meResponse.statusCode).toBe(200);
    });

    it('should return 401 when an access token is presented as the refresh cookie', async () => {
      // Signed with JWT_SECRET — fails verification against JWT_REFRESH_SECRET
      const accessToken = app.jwt.sign({ userId: 'u1', email: 'user@example.com', type: 'access' });

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/refresh',
        headers: { cookie: `refreshToken=${accessToken}` },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 401 when a refresh-secret token is missing the refresh type claim', async () => {
      // Correct secret, wrong (absent) type — exercises the explicit type check
      const typelessToken = app.jwt.refresh.sign({ userId: 'u1' });

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/refresh',
        headers: { cookie: `refreshToken=${typelessToken}` },
      });

      expect(response.statusCode).toBe(401);
    });
  });

  describe('registration toggle (ALLOW_REGISTRATION)', () => {
    const originalAllowRegistration = config.ALLOW_REGISTRATION;

    afterEach(() => {
      config.ALLOW_REGISTRATION = originalAllowRegistration;
    });

    it('should report registration as open by default', async () => {
      const response = await app.inject({ method: 'GET', url: '/api/auth/registration' });

      expect(response.json()).toEqual({ open: true });
    });

    it('should report registration as closed when ALLOW_REGISTRATION is false', async () => {
      config.ALLOW_REGISTRATION = false;

      const response = await app.inject({ method: 'GET', url: '/api/auth/registration' });

      expect(response.json()).toEqual({ open: false });
    });

    it('should return 403 REGISTRATION_CLOSED without creating a user when registration is closed', async () => {
      config.ALLOW_REGISTRATION = false;

      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/register',
        // Own IP so this call does not share the register route's 5/hour counter
        remoteAddress: '203.0.113.10',
        headers: { 'content-type': 'application/json' },
        payload: { email: 'closed@example.com', password: 'SecurePass123', name: 'Closed' },
      });

      expect(response.statusCode).toBe(403);
      expect(response.json()).toMatchObject({ error: 'REGISTRATION_CLOSED' });
      expect(mockContainer.authService.register).not.toHaveBeenCalled();
    });
  });

  describe('POST /api/auth/refresh rate limiting', () => {
    it('should return 429 RATE_LIMITED after 30 attempts within the window (docs/SECURITY.md: 30 / 15 min, IP-scoped)', async () => {
      // Fresh app so the in-memory per-route counter starts at zero and the
      // shared app's refresh tests are not polluted by 31 extra hits.
      const freshApp = await buildTestApp(createMockContainer());
      await freshApp.ready();
      try {
        for (let i = 0; i < 30; i++) {
          const response = await freshApp.inject({ method: 'POST', url: '/api/auth/refresh' });
          // No cookie → REFRESH_EXPIRED, but still under the limit.
          expect(response.statusCode).toBe(401);
        }

        const limited = await freshApp.inject({ method: 'POST', url: '/api/auth/refresh' });
        expect(limited.statusCode).toBe(429);
        expect(limited.json()).toMatchObject({ error: 'RATE_LIMITED' });
      } finally {
        await freshApp.close();
      }
    });
  });

  describe('token type separation', () => {
    it('should return 401 when a refresh token is presented as a Bearer token', async () => {
      // Signed with JWT_REFRESH_SECRET — fails verification against JWT_SECRET
      const refreshToken = app.jwt.refresh.sign({ userId: 'test-user-id', type: 'refresh' });

      const response = await app.inject({
        method: 'GET',
        url: '/api/auth/me',
        headers: { authorization: `Bearer ${refreshToken}` },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 401 when an access-secret token carries type refresh', async () => {
      // Valid signature, wrong type — exercises the authenticate type guard
      const confusedToken = app.jwt.sign({ userId: 'test-user-id', type: 'refresh' });

      const response = await app.inject({
        method: 'GET',
        url: '/api/auth/me',
        headers: { authorization: `Bearer ${confusedToken}` },
      });

      expect(response.statusCode).toBe(401);
    });

    it('should accept a legacy token without a type claim as an access token', async () => {
      // Migration window: tokens issued before the type claim existed still work
      const mockUser = { id: 'test-user-id', email: 'test@example.com' };
      mockContainer.authService.getUser.mockResolvedValue(mockUser);
      const legacyToken = app.jwt.sign({ userId: 'test-user-id', email: 'test@example.com' });

      const response = await app.inject({
        method: 'GET',
        url: '/api/auth/me',
        headers: { authorization: `Bearer ${legacyToken}` },
      });

      expect(response.statusCode).toBe(200);
    });
  });

  describe('POST /api/auth/logout', () => {
    it('should clear refresh token cookie', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/auth/logout',
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ success: true });
      // Check that set-cookie header clears the refreshToken
      const cookies = response.headers['set-cookie'];
      expect(cookies).toBeDefined();
    });
  });

  describe('GET /api/auth/me', () => {
    it('should return current user info', async () => {
      const mockUser = { id: 'test-user-id', email: 'test@example.com' };
      mockContainer.authService.getUser.mockResolvedValue(mockUser);

      const response = await app.inject({
        method: 'GET',
        url: '/api/auth/me',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.authService.getUser).toHaveBeenCalledWith('test-user-id');
      expect(response.json()).toEqual(mockUser);
    });

    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'GET',
        url: '/api/auth/me',
      });

      expect(response.statusCode).toBe(401);
    });

    it('should return 404 when user not found', async () => {
      const { UserNotFoundError } = await import('../utils/errors.js');
      mockContainer.authService.getUser.mockRejectedValue(new UserNotFoundError());

      const response = await app.inject({
        method: 'GET',
        url: '/api/auth/me',
        headers: { authorization: authHeader },
      });

      expect(response.statusCode).toBe(404);
    });
  });
});
