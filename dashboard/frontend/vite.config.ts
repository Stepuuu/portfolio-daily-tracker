import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const frontendPort = Number(env.FRONTEND_PORT || '3000')
  const backendPort = Number(env.BACKEND_PORT || '8000')
  // A wildcard bind address (BACKEND_HOST=0.0.0.0) is not a name the backend
  // answers to; the proxy reaches that server through loopback instead.
  const bindHost = env.BACKEND_HOST || '127.0.0.1'
  const backendHost = ['0.0.0.0', '::', '[::]'].includes(bindHost) ? '127.0.0.1' : bindHost

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: frontendPort,
      host: env.FRONTEND_HOST || '127.0.0.1',
      strictPort: true,
      proxy: {
        '/api': {
          target: `http://${backendHost}:${backendPort}`,
          changeOrigin: true,
        },
      },
    },
  }
})
