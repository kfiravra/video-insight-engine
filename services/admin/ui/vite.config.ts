import type { IncomingMessage } from 'node:http'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const BACKEND = 'http://localhost:8002'

/**
 * Several proxied prefixes double as SPA routes (/users, /usage, /health,
 * /alerts). A page navigation — hard refresh or deep link — asks for HTML, so
 * hand it to Vite's index.html instead of the backend; XHR keeps proxying.
 */
const proxyRule = {
  target: BACKEND,
  bypass: (req: IncomingMessage) => (req.headers.accept?.includes('text/html') ? '/index.html' : undefined),
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    globals: true,
    testTimeout: 15_000,
    exclude: ['e2e/**', 'node_modules/**'],
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    // Every backend router prefix (src/main.py include_router + app-level
    // routes). Missing entries fall through to the SPA index.html, which
    // is how the dev UI used to be unable to log in (/auth) or load the
    // Users / Shares / Tiers / Queue panels.
    proxy: Object.fromEntries(
      ['/auth', '/usage', '/health', '/alerts', '/shares', '/tiers', '/users', '/videos', '/queue', '/admin'].map(
        (prefix) => [prefix, proxyRule],
      ),
    ),
  },
})
