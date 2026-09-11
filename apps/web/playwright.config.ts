import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const webRoot = fileURLToPath(new URL('.', import.meta.url))
const projectRoot = path.resolve(webRoot, '../..')
const sourcePath = path.join(projectRoot, 'tmp', 'e2e-source.mp4')
const sourceRoot = path.join(projectRoot, 'tmp', 'e2e-sources')
const sourceManifestPath = path.join(projectRoot, 'tmp', 'e2e-source-manifest.json')

function readPort(name: string, fallback: string): string {
  const value = process.env[name] ?? fallback
  const port = Number(value)
  if (!/^\d+$/.test(value) || !Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`${name} must be an integer TCP port`)
  }
  return value
}

const apiPort = readPort('TRAINPAL_E2E_API_PORT', '18123')
const webPort = readPort('TRAINPAL_E2E_WEB_PORT', '15173')
const apiOrigin = `http://127.0.0.1:${apiPort}`
const webOrigin = `http://127.0.0.1:${webPort}`

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  // E2E files share the product's single global analysis slot in one API process.
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: webOrigin,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'mobile-chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 },
      },
    },
  ],
  webServer: [
    {
      command: `uv run --project ../../services/analysis-api uvicorn hakimi_analysis.main:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: webRoot,
      url: `${apiOrigin}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: 'test',
        ANALYSIS_PROVIDER: 'test',
        CORS_ORIGINS: webOrigin,
        HAKIMI_DEMO_VIDEO_PATH: sourcePath,
        SOURCE_MANIFEST_PATH: sourceManifestPath,
        SOURCE_MEDIA_ROOT: sourceRoot,
        PUBLIC_MEDIA_BASE_URL: `https://127.0.0.1:${apiPort}/e2e-media`,
      },
    },
    {
      command: `pnpm dev --host 127.0.0.1 --port ${webPort}`,
      cwd: webRoot,
      url: webOrigin,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        VITE_API_TARGET: apiOrigin,
      },
    },
  ],
})
