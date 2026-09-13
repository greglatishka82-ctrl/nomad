import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // In Vercel, /api/admin is served by api/admin/[...path].js and the target
  // comes from the server-only ADMIN_BACKEND_URL variable.  Keep an optional
  // local proxy, but never bake a Render URL into the client build.
  const apiTarget = env.ADMIN_BACKEND_URL || env.VITE_API_URL

  return {
    root: '.',
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      rollupOptions: {
        input: {
          main: './index.html'
        }
      }
    },
    // Production uses the Vercel Function. The local proxy gives the same
    // /api/admin origin locally, so cookies and every UI action work in dev.
    server: apiTarget ? {
      port: 3000,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        }
      }
    } : { port: 3000 }
  }
})
