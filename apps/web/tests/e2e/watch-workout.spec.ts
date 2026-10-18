import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

for (const width of [320, 390, 768, 1440]) {
  test(`watch workout preserves tips, loops and auto advances at ${width}px`, async ({ page }) => {
    test.setTimeout(90_000)
    await page.setViewportSize({ width, height: width < 768 ? 844 : 900 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    // Synthetic analysis and generated video only; no real provider calls.
    const media = await readFile(fileURLToPath(new URL('../../../../tmp/e2e-sources/e2e-source-01.mp4', import.meta.url)))
    await page.route('**/api/v1/sources/*/media', async route => {
      const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/)
      const start = range ? Number(range[1]) : 0
      const end = range?.[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1
      await route.fulfill({ status: range ? 206 : 200, contentType: 'video/mp4', body: media.subarray(start, end + 1),
        headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${media.length}` } : {}) },
      })
    })
    const output = path.join(tmpdir(), 'trainpal-workout-ux-20261007', process.env.TRAINPAL_UX_AUDIT_STAGE ?? 'candidate')
    await mkdir(output, { recursive: true })
    const capture = async (stage: string) => {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await page.screenshot({ path: path.join(output, `${width}-${stage}.png`), fullPage: true })
    }
    const result = {
      id: 'watch-fixture', source_id: 'e2e-source-01', trigger_seconds: null,
      status: 'completed', stage: 'completed', warnings: [], empty_reason: null, error: null,
      source_duration_seconds: 2, processed_seconds: 2, discovered_candidate_count: 2,
      coverage_status: 'complete', coverage_gaps: [], created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:01Z',
      candidates: [
        { id: 'first', source_id: 'e2e-source-01', name: '测试俯卧撑', segment: { start_seconds: 0, end_seconds: 1.9 },
          parameters: { mode: 'reps', sets: 2, reps: 3, duration_seconds: null, rest_seconds: 5 },
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
    await page.locator('.quick-real-section > summary').click()
  await page.locator('.quick-video-branch > summary').click()
    await page.getByRole('button', { name: '0:02', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
    await page.getByRole('button', { name: '查看训练方案' }).click()
    await expect(page).toHaveURL(/\/plan$/)
    await expect(page.getByLabel('方案摘要')).toContainText('2 个可训练动作')
    await capture('plan')
    await page.getByRole('button', { name: '开始训练', exact: true }).click()
    await expect(page).toHaveURL(/\/training$/)
    await expect(page.getByLabel('本次训练进度')).toContainText('已完成 0 / 3 组')
    await capture('ready')
    await page.getByRole('button', { name: '准备好了', exact: true }).click()
    await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
    await expect(page.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
    const video = page.locator('.media-stage video')
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) =>
      element.readyState >= 2 && element.videoWidth === 390 && element.videoHeight === 694
      && element.getVideoPlaybackQuality().totalVideoFrames > 0 && !element.paused && !element.seeking,
    )).toBe(true)
    const pixel = await video.evaluate((element: HTMLVideoElement) => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 1
      const context = canvas.getContext('2d')!
      context.drawImage(element, 0, 0, 1, 1)
      return Array.from(context.getImageData(0, 0, 1, 1).data)
    })
    expect(pixel[0]).toBeGreaterThan(8)
    expect(pixel[1]).toBeGreaterThan(pixel[0]!)
    expect(pixel[2]).toBeGreaterThan(pixel[1]!)
    expect(pixel[3]).toBe(255)
    await page.evaluate(() => window.scrollTo(0, 0))
    await capture('training')
    await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeInViewport({ ratio: 1 })
    const targetBox = (await page.locator('.status-readout').boundingBox())!
    const controlsBox = (await page.locator('.training-controls').boundingBox())!
    expect(targetBox.y + targetBox.height).toBeLessThanOrEqual(controlsBox.y)
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    await expect(page.locator('.status-pill')).toHaveText('训练已暂停')
    await capture('paused')
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([])
    await page.getByRole('button', { name: '继续训练', exact: true }).click()
    await page.getByRole('button', { name: '完成本组', exact: true }).click()
    await expect(page.locator('.status-pill')).toHaveText('组间休息')
    await expect(page.getByLabel('接下来的安排')).toContainText('测试俯卧撑 · 第 2 / 2 组 · 3 次')
    await expect(page.getByLabel('本次训练进度')).toContainText('已完成 1 / 3 组')
    await capture('rest')
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    await expect(page.locator('.status-pill')).toHaveText('休息已暂停')
    await capture('paused-rest')
    await page.getByRole('button', { name: '继续休息', exact: true }).click()
    await expect(page.locator('.status-pill')).toHaveText('准备开始', { timeout: 8_000 })
    await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible({ timeout: 5_000 })
    await page.getByRole('button', { name: '完成本组', exact: true }).click()
    await expect(page.locator('.status-pill')).toHaveText('组间休息')
    await expect(page.getByLabel('接下来的安排')).toContainText('测试支撑 · 第 1 / 1 组 · 1 秒')
    await expect(page.getByText('接下来换动作。休息结束后，点“准备好了”再开始。')).toBeVisible()
    await capture('between-actions-rest')
    await expect(page.getByRole('button', { name: '准备好了', exact: true })).toBeVisible({ timeout: 7_000 })
    await expect(page.getByRole('heading', { name: '测试支撑', exact: true })).toBeVisible()
    await capture('next-action')
    await page.getByRole('button', { name: '准备好了', exact: true }).click()
    await expect(page.getByRole('heading', { name: '训练完成', exact: true })).toBeVisible({ timeout: 7_000 })
    await page.getByRole('link', { name: '查看训练结果' }).click()
    await expect(page).toHaveURL(/\/result\//)
    await expect(page.getByLabel('实际完成组数')).toHaveText('已完成 3 / 3 组')
    await capture('result')
    await page.getByRole('button', { name: '再练一次' }).click()
    await page.reload()
    await page.getByRole('button', { name: '开始训练', exact: true }).click()
    await expect(page.getByRole('heading', { name: '测试俯卧撑', exact: true })).toBeVisible()
    expect(errors).toEqual([])
  })
}
