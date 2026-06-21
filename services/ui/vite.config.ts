import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  esbuild: {
    jsx: 'automatic',
  },
  optimizeDeps: {
    esbuildOptions: {
      jsx: 'automatic',
    },
  },
  build: {
    minify: 'esbuild',
    commonjsOptions: {
      transformMixedEsModules: true,
    },
  },
  resolve: {
    alias: process.env.VITEST
      ? {
          '@monaco-editor/react': path.resolve(__dirname, 'src/test/__mocks__/@monaco-editor/react.tsx'),
          '@monaco-editor/loader': path.resolve(__dirname, 'src/test/__mocks__/@monaco-editor/loader.ts'),
        }
      : {},
  },
  test: {
    globals: true,
    environment: 'happy-dom',
    setupFiles: './src/test/setup.ts',
    exclude: [
      '**/e2e/**/*.spec.ts',
      '**/node_modules/**',
      '**/dist/**',
      '**/android/**',
    ],
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://192.168.2.205:8080',
        changeOrigin: true,
        ws: true,
      },
      '/health': {
        target: 'http://192.168.2.205:8080',
        changeOrigin: true,
      },
      '/v1': {
        target: 'http://192.168.2.205:8080',
        changeOrigin: true,
      },
    }
  }
})
