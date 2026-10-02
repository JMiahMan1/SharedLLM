import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { execSync } from 'child_process'

// Resolve the git SHA at config-load time so it can be embedded into the
// bundle as __BUILD_SHA__ (used by the OTA updater as a runtime fallback).
const buildSha: string = process.env.GIT_SHA || (() => {
  try {
    return execSync('git rev-parse --short HEAD').toString().trim()
  } catch {
    return 'unknown'
  }
})()

// https://vite.dev/config/
export default defineConfig({
  define: {
    __BUILD_SHA__: JSON.stringify(buildSha),
  },
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
    rollupOptions: {
      input: {
        main: './index.html',
      },
      output: {
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
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
    // These render whole pages (msw + providers + charts), so they lose the
    // race for CPU when ~80 files run in parallel and were blowing vitest's
    // default 5s budget. Every failure was a bare "Test timed out in 5000ms",
    // never an assertion, and each file passed on its own. 20s keeps a real
    // hang failing rather than hanging forever.
    testTimeout: 20000,
    hookTimeout: 20000,
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
