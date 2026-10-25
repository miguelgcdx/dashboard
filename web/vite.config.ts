// Node runtime builtins; this frontend package intentionally has no @types/node dependency.
// @ts-expect-error -- Node provides this module when Vite loads its config.
import { execFile } from 'node:child_process';
// @ts-expect-error -- Node provides this module when Vite loads its config.
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import type { Plugin } from 'vite';

const snapshotPath = '/__pi_usage_snapshot';
const dashboardPath: string = fileURLToPath(new URL('../dashboard.py', import.meta.url));
const webPath: string = fileURLToPath(new URL('.', import.meta.url));
const loopbackHosts = new Set(['127.0.0.1:5173', 'localhost:5173', '[::1]:5173']);

function localUsage(): Plugin {
  return {
    name: 'local-usage-snapshot',
    apply: 'serve',
    configureServer(server) {
      let pending: Promise<string> | undefined;
      const shutdown = new AbortController();
      server.httpServer?.once('close', () => shutdown.abort());
      const collect = (): Promise<string> => {
        // Only overlap is shared: a later page reload always collects fresh data.
        pending ??= new Promise<string>((resolve, reject) => {
          execFile('python3', ['-B', dashboardPath, '--dev-json'], {
            encoding: 'utf8', timeout: 30_000, maxBuffer: 16 * 1024 * 1024,
            shell: false, killSignal: 'SIGKILL', signal: shutdown.signal,
          }, (error: Error | null, stdout: string) => {
            // Discard stderr and errors: they may contain local paths or records.
            if (error) reject(new Error('Usage snapshot unavailable.'));
            else resolve(stdout);
          });
        }).finally(() => { pending = undefined; });
        return pending;
      };
      server.middlewares.use((request, response, next) => {
        // Vite's Node request fields are not declared by this browser-only TS setup.
        const incoming = request as typeof request & {
          headers: { host?: string; origin?: string }; url?: string; method?: string;
        };
        const host = incoming.headers.host;
        const origin = incoming.headers.origin;
        // Run before Vite's static handlers too. Never trust forwarded headers.
        if (!host || !loopbackHosts.has(host) ||
            (origin !== undefined && origin !== `http://${host}`)) {
          response.writeHead(403, {
            'Content-Type': 'text/plain; charset=utf-8',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
          });
          response.end('Local same-origin requests only.');
          return;
        }
        if (incoming.url !== snapshotPath) return next();
        response.setHeader('Content-Type', 'application/json; charset=utf-8');
        response.setHeader('Cache-Control', 'no-store');
        response.setHeader('X-Content-Type-Options', 'nosniff');
        response.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
        if (incoming.method !== 'GET') {
          response.setHeader('Allow', 'GET');
          response.writeHead(405);
          response.end(JSON.stringify({ error: 'GET required.' }));
          return;
        }
        void collect().then((snapshot) => {
          if (!response.destroyed) response.end(snapshot);
        }, () => {
          if (!response.destroyed) {
            response.writeHead(503);
            response.end(JSON.stringify({ error: 'Usage snapshot unavailable. Reload to retry.' }));
          }
        });
      });
    },
  };
}

export default defineConfig(({ command }) => ({
  plugins: [localUsage()],
  publicDir: false,
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    cors: false,
    // Do not widen serving to the root command runner or any private output.
    fs: { strict: true, allow: [webPath] },
  },
  // Library mode emits one classic script: no module fetches on file://.
  define: { 'process.env.NODE_ENV': JSON.stringify(command === 'build' ? 'production' : 'development') },
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: false,
    cssCodeSplit: false,
    lib: {
      entry: 'src/main.tsx',
      name: 'PiUsageDashboard',
      formats: ['iife'],
      fileName: () => 'dashboard.js',
      cssFileName: 'dashboard',
    },
    rollupOptions: { external: [] },
  },
}));
