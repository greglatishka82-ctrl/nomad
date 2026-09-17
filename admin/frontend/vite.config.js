import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // На боевом сервере панель и API живут на одном домене: запрос /api/admin
  // проксирует nginx (deploy/admin-frontend.conf). Переменная ниже нужна
  // только для запуска dev-сервера и в сборку не попадает.
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
    // Боевая сборка отдаётся nginx и работает со своего домена. Прокси нужен
    // только для dev-сервера, чтобы cookie и действия панели работали локально.
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
