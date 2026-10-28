import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    // scripts/ sits outside this package, so its bare imports would not find the
    // drivers installed here; resolving them from the api root keeps one copy.
    dedupe: ['bcrypt', 'mongodb'],
  },
  test: {
    globals: true,
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.spec.ts', '../scripts/**/*.test.ts'],
    setupFiles: ['./src/test/env.ts', './src/test/setup.ts'],
    globalSetup: ['./src/test/global-setup.ts'],
    testTimeout: 30000,
    hookTimeout: 30000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: [
        'node_modules',
        'dist',
        'src/test/**',
        '**/*.d.ts',
      ],
    },
  },
});
