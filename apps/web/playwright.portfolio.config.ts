import { defineConfig } from '@playwright/test'
import config from './playwright.config'

export default defineConfig({
  ...config,
  testDir: './tests/portfolio-e2e',
  webServer: (Array.isArray(config.webServer) ? config.webServer : []).map(server => ({
    ...server,
    env: { ...server.env, TRAINPAL_BASE_PATH: '/trainpal/', PUBLIC_BASE_PATH: '/trainpal' },
  })),
})
