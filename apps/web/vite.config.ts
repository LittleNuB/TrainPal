import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const base = process.env.TRAINPAL_BASE_PATH ?? '/'
if (!/^\/(?:[a-zA-Z0-9_-]+\/)*$/.test(base)) throw new Error('Invalid TRAINPAL_BASE_PATH')

export default defineConfig({
  base,
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      [`${base.replace(/\/$/, '')}/api`]: {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        rewrite: path => base === '/' ? path : path.slice(base.length - 1),
      },
    },
  },
  test: {
    maxWorkers: 2,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['./tests/*.spec.ts'],
    restoreMocks: true,
  },
})
