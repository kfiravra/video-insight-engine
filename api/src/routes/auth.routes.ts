import { FastifyInstance, FastifyReply } from 'fastify';
import { z } from 'zod';
import { registerSchema, loginSchema, demoLoginSchema } from '../schemas/auth.schema.js';
import { config } from '../config.js';
import { DemoDisabledError, RefreshExpiredError, RegistrationClosedError } from '../utils/errors.js';
import type { LoginInput } from '../schemas/auth.schema.js';

const ACCESS_TOKEN_EXPIRY_SECONDS = 900;
const REFRESH_COOKIE_MAX_AGE = 7 * 24 * 60 * 60; // 7 days

interface TokenUser {
  id: string;
  email: string;
}

/**
 * Generate access and refresh tokens, set refresh token cookie
 */
function generateAuthTokens(
  fastify: FastifyInstance,
  reply: FastifyReply,
  user: TokenUser
): { accessToken: string; expiresIn: number } {
  const accessToken = fastify.jwt.sign(
    { userId: user.id, email: user.email, type: 'access' },
    { expiresIn: config.JWT_EXPIRES_IN }
  );

  // Refresh tokens are signed with JWT_REFRESH_SECRET (separate @fastify/jwt
  // namespace) and carry `type: 'refresh'` so neither token can stand in for
  // the other — see plugins/jwt.ts.
  const refreshToken = fastify.jwt.refresh.sign(
    { userId: user.id, type: 'refresh' },
    { expiresIn: config.JWT_REFRESH_EXPIRES_IN }
  );

  reply.setCookie('refreshToken', refreshToken, {
    httpOnly: true,
    secure: config.NODE_ENV === 'production',
    sameSite: 'strict',
    path: '/api/auth/refresh',
    maxAge: REFRESH_COOKIE_MAX_AGE,
  });

  return { accessToken, expiresIn: ACCESS_TOKEN_EXPIRY_SECONDS };
}

/**
 * Demo account credentials, or null when demo mode is off. Read per call so
 * the env pair is the single switch: both set = on, either blank = off.
 */
function demoCredentials(): LoginInput | null {
  const { DEMO_USER_EMAIL: email, DEMO_USER_PASSWORD: password } = config;
  return email && password ? { email, password } : null;
}

/**
 * Resolve the credentials for a login request. `{ demo: true }` swaps in the
 * server-held demo account so its password never travels to the browser;
 * anything else is validated as a normal email + password login.
 */
function resolveLoginInput(body: unknown): LoginInput {
  if (!demoLoginSchema.safeParse(body).success) {
    return loginSchema.parse(body);
  }
  const credentials = demoCredentials();
  if (!credentials) {
    throw new DemoDisabledError();
  }
  return credentials;
}

export async function authRoutes(fastify: FastifyInstance) {
  const { authService } = fastify.container;

  // POST /api/auth/register
  // TODO: v1.5 — Accept optional referralSlug in registration body, store on user record
  // Frontend: if user came from /s/:slug, include slug in registration request
  fastify.post<{
    Body: z.infer<typeof registerSchema>;
  }>('/register', {
    config: {
      rateLimit: { max: 5, timeWindow: '1 hour' },
    },
  }, async (req, reply) => {
    if (!config.ALLOW_REGISTRATION) {
      throw new RegistrationClosedError();
    }
    const input = registerSchema.parse(req.body);
    const user = await authService.register(input);
    const tokens = generateAuthTokens(fastify, reply, user);
    return reply.code(201).send({ ...tokens, user });
  });

  // GET /api/auth/registration
  // Public: lets the prebuilt web bundle hide signup and show the demo entry
  // at runtime instead of baking the flags in at image build time.
  fastify.get('/registration', async () => ({
    open: config.ALLOW_REGISTRATION,
    demoEnabled: demoCredentials() !== null,
  }));

  // POST /api/auth/login
  // Body is { email, password }, or { demo: true } for the demo account. Both
  // share this route's rate limit and the same AuthService.login path.
  fastify.post<{
    Body: z.infer<typeof loginSchema> | z.infer<typeof demoLoginSchema>;
  }>('/login', {
    config: {
      rateLimit: { max: 10, timeWindow: '15 minutes' },
    },
  }, async (req, reply) => {
    const input = resolveLoginInput(req.body);
    const user = await authService.login(input);
    const tokens = generateAuthTokens(fastify, reply, user);
    return { ...tokens, user };
  });

  // POST /api/auth/refresh
  // docs/SECURITY.md: 30 / 15 min, IP-scoped — pre-auth route, so the global
  // preHandler keyGenerator falls back to the `ip:` key. Bounds cookie-
  // guessing and refresh-storm loops without throttling legitimate tab herds.
  fastify.post('/refresh', {
    config: {
      rateLimit: { max: 30, timeWindow: '15 minutes' },
    },
  }, async (req, reply) => {
    const refreshToken = req.cookies.refreshToken;
    if (!refreshToken) {
      throw new RefreshExpiredError();
    }

    try {
      // Verified against JWT_REFRESH_SECRET — an access token (JWT_SECRET)
      // presented here fails signature verification. The explicit type check
      // is defence in depth against any same-secret token slipping through.
      const payload = fastify.jwt.refresh.verify<{ userId: string; type?: string }>(refreshToken);
      if (payload.type !== 'refresh') {
        throw new RefreshExpiredError();
      }

      const accessToken = fastify.jwt.sign(
        { userId: payload.userId, type: 'access' },
        { expiresIn: config.JWT_EXPIRES_IN }
      );

      return { accessToken, expiresIn: ACCESS_TOKEN_EXPIRY_SECONDS };
    } catch {
      reply.clearCookie('refreshToken', { path: '/api/auth/refresh' });
      throw new RefreshExpiredError();
    }
  });

  // POST /api/auth/logout
  fastify.post('/logout', async (req, reply) => {
    reply.clearCookie('refreshToken', { path: '/api/auth/refresh' });
    return { success: true };
  });

  // GET /api/auth/me
  fastify.get('/me', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    return authService.getUser(req.user.userId);
  });
}
