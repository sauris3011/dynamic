import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

/**
 * The UI talks only to the Pricing Platform on :8000 (PRD 4.1). It never
 * contacts the Commerce Service and never contacts the LLM gateway — gateway
 * credentials must not exist on the client at all (FR-072, NFR-011).
 *
 * NODE_TLS_REJECT_UNAUTHORIZED is set here, in the dev proxy only, and never in
 * production build output (NFR-020). It is also gated behind the same explicit
 * flag the backend requires, so an insecure posture is a deliberate act rather
 * than an inherited default.
 */
// Minimal ambient declaration rather than a dependency on @types/node: this is
// the only Node surface the config touches, and pulling in the full Node type
// package for two properties is not a trade worth making.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const target = env.VITE_API_URL || 'http://127.0.0.1:8000';

  if (env.ALLOW_INSECURE_TLS === 'true' && mode === 'development') {
    process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
    console.warn(
      '[vite] TLS verification disabled for the dev proxy. ' +
        'Man-in-the-middle protection is off. Never use this in a build.',
    );
  }

  return {
    plugins: [react()],
    server: {
      // Bound explicitly to IPv4 loopback. Vite's default `localhost` resolves
      // to ::1 on Windows, which leaves anything asking for 127.0.0.1 —
      // including the startup script's health probe — with a refused
      // connection and no obvious cause.
      host: '127.0.0.1',
      port: Number(env.UI_PORT) || 5173,
      strictPort: true,
      proxy: {
        '/api': { target, changeOrigin: true },
        '/health': { target, changeOrigin: true },
        '/a2a': { target, changeOrigin: true },
        '/.well-known': { target, changeOrigin: true },
      },
    },
    build: { outDir: 'dist', sourcemap: false },
  };
});
