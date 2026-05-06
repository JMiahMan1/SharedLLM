import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  build: {
    minify: false
  },
  server: {
    proxy: {
      '/api/auth': 'http://localhost:8001',
      '/api/users': 'http://localhost:8001',
      '/api/resolve': 'http://localhost:8001',
      '/api/chat': 'http://localhost:11435',
      '/v1': 'http://localhost:11435',
      '/api/logs': 'http://localhost:8006',
      '/api/logs/stream': {
        target: 'ws://localhost:8006',
        ws: true
      },
      '/execute': 'http://localhost:11436',
      '/api/admin': 'http://localhost:11438',
      '/api/workspace': 'http://localhost:11438',
      '/health': 'http://localhost:11435',
    }
  }
})
