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
      '/api': {
        target: 'http://localhost:11435',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:11435',
        changeOrigin: true,
      }
    }
  }
})
