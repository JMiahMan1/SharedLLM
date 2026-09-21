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
        'ma-stream-test': './src/ma-stream-test.ts',
      },
      output: {
        entryFileNames: (chunkInfo) => {
          if (chunkInfo.name === 'ma-stream-test') return 'assets/ma-stream-test.js'
          return 'assets/[name]-[hash].js'
        },
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
