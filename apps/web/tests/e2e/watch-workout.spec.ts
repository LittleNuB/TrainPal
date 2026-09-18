import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

for (const width of [390, 1440]) {
  test(`watch workout preserves tips, loops and auto advances at ${width}px`, async ({ page }) => {
    test.setTimeout(60_000)
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/v1/sources/*/media', route => route.fulfill({
      path: fileURLToPath(new URL('../../../../tmp/e2e-sources/e2e-source-01.mp4', import.meta.url)), contentType: 'video/mp4',
    }))
    const result = {
      id: 'watch-fixture', source_id: 'e2e-source-01', trigger_seconds: null,
      status: 'completed', stage: 'completed', warnings: [], empty_reason: null, error: null,
      source_duration_seconds: 2, processed_seconds: 2, discovered_candidate_count: 2,
      coverage_status: 'complete', coverage_gaps: [], created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:01Z',
      candidates: [
        { id: 'first', source_id: 'e2e-source-01', name: '测试俯卧撑', segment: { start_seconds: 0, end_seconds: 1.9 },
          parameters: { mode: 'reps', sets: 2, reps: 3, duration_seconds: null, rest_seconds: 1 },
          evidence: [{ type: 'speech', start_seconds: 0, end_seconds: 1.9 }], needs_confirmation: false,
          tips: [{ text: '推起时呼气', category: 'breathing', evidence: { type: 'speech', start_seconds: 0.3, end_seconds: 0.8 } }],
        },
        { id: 'second', source_id: 'e2e-source-01', name: '测试支撑', segment: { start_seconds: 0, end_seconds: 1.9 },
          parameters: { mode: 'duration', sets: 1, reps: null, duration_seconds: 1, rest_seconds: 0 },
          evidence: [{ type: 'visual', start_seconds: 0, end_seconds: 1.9 }], needs_confirmation: false,
        },
      ],
    }
    await page.route('**/api/v1/analysis-runs', route => route.fulfill({ status: 202, json: { ...result, status: 'queued', stage: 'queued', candidates: [] } }))
    await page.route('**/api/v1/analysis-runs/watch-fixture', route => route.fulfill({ json: result }))
    await page.route('**/api/v1/analysis-runs/watch-fixture/events', route => route.fulfill({ contentType: 'text/event-stream', body: `event: run.completed\ndata: ${JSON.stringify({ sequence: 1, type: 'run.completed', run_id: result.id, data: { stage: 'completed', source_duration_seconds: 2, processed_seconds: 2, discovered_candidate_count: 2, coverage_status: 'complete', coverage_gaps: [] } })}\n\n` }))
    await page.goto('/')
    await expect(page).toHaveTitle(/TrainPal/)
    await page.locator('.quick-real-section summary').click()
    await page.getByRole('button', { name: '0:02', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
    await page.getByRole('button', { name: '查看训练方案' }).click()
    await page.getByRole('button', { name: '开始训练', exact: true }).click()
    await expect(page).toHaveURL(/\/training$/)
    await page.getByRole('button', { name: '准备好了', exact: true }).click()
    await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
    await expect(page.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
    const output = path.join(tmpdir(), 'trainpal-watch-workout-20260918')
    await mkdir(output, { recursive: true })
    await page.screenshot({ path: path.join(output, `${width}-training.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.getByRole('button', { name: '完成本组', exact: true }).click()
    await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible({ timeout: 7_000 })
    await page.getByRole('button', { name: '完成本组', exact: true }).click()
    await expect(page.getByRole('button', { name: '准备好了', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '测试支撑', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '准备好了', exact: true }).click()
    await expect(page.getByRole('heading', { name: '训练完成', exact: true })).toBeVisible({ timeout: 7_000 })
    await page.getByRole('link', { name: '查看训练结果' }).click()
    await page.getByRole('button', { name: '再练一次' }).click()
    await page.reload()
    await page.getByRole('button', { name: '开始训练', exact: true }).click()
    await expect(page.getByRole('heading', { name: '测试俯卧撑', exact: true })).toBeVisible()
    expect(errors).toEqual([])
  })
}
