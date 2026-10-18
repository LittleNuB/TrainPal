import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

test.use({ video: { mode: 'on', size: { width: 390, height: 844 } } })

// This journey uses synthetic provider evidence and media in the isolated E2E browser.
// All editing, training, profile and feedback operations use the real app and IndexedDB.
test('approved sport AI journey produces 18 real screens and editable personal suggestions', async ({ page }) => {
  test.setTimeout(180_000)
  page.setDefaultTimeout(10_000)
  const output = path.resolve(process.cwd(), '../../tmp/audit/sport-ai-20261009')
  await mkdir(output, { recursive: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const captures: Array<{ id: string; url: string; width: number; file: string }> = []
  const capture = async (id: string, responsive = false) => {
    for (const width of responsive ? [320, 390, 768, 1440] : [390]) {
      await page.setViewportSize({ width, height: width < 768 ? 844 : 900 })
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), { message: `${id} at ${width}` }).toBe(true)
      const file = `${id}-${width}.png`
      await page.screenshot({ path: path.join(output, file), fullPage: true })
      captures.push({ id, width, file, url: new URL(page.url()).pathname })
    }
    await page.setViewportSize({ width: 390, height: 844 })
  }
  await page.goto('/personalize')
  await expect(page.locator('[data-option-id]').first()).toBeVisible()
  await capture('15-gymti-question', true)
  for (let index = 0; index < 7; index++) {
    await page.locator('button[data-option-id$="_no_match"]').click()
    await expect(page.getByText('TrainPal 正在挑下一题…')).toBeHidden()
  }
  await page.locator('button[data-option-id]').first().click()
  await expect(page.getByRole('heading', { name: '完善训练档案' })).toBeVisible()
  await capture('14-optional-profile', true)
  await page.getByRole('button', { name: '跳过', exact: true }).click()
  await expect(page.locator('.gymti-art img')).toBeVisible()
  await capture('16-gymti-result', true)
  await page.getByRole('button', { name: '修改风格' }).click()
  await page.locator('[data-style-id="gentle"]').click()
  await capture('17-coach-picker', true)
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations).toEqual([])
  await page.locator('.style-action button').click()
  await expect(page).toHaveURL(/\/plan$/)

  const mediaPath = path.resolve(process.cwd(), '../../tmp/e2e-sources/e2e-source-01.mp4')
  const media = await readFile(mediaPath)
  await page.route('**/api/v1/sources/*/media', route => route.fulfill({ contentType: 'video/mp4', body: media }))
  let completed = false
  let finishRun!: () => void
  const finished = new Promise<void>(resolve => { finishRun = resolve })
  const now = '2026-10-09T00:00:00Z'
  const result = {
    id: 'sport-ai-audit', source_id: 'e2e-source-01', trigger_seconds: null,
    status: 'completed', stage: 'completed', warnings: [], empty_reason: null, error: null,
    source_duration_seconds: 2, processed_seconds: 2, discovered_candidate_count: 2,
    coverage_status: 'complete', coverage_gaps: [], created_at: now, updated_at: now,
    candidates: [
      { id: 'pushup', source_id: 'e2e-source-01', name: '俯卧撑', segment: { start_seconds: 0, end_seconds: 1.9 },
        parameters: { mode: 'reps', sets: 2, reps: null, duration_seconds: null, rest_seconds: 15 },
        evidence: [{ type: 'speech', start_seconds: 0, end_seconds: 1.9 }], needs_confirmation: false,
        tips: [{ text: '推起时呼气', category: 'breathing', evidence: { type: 'speech', start_seconds: 0.3, end_seconds: 0.8 } }] },
      { id: 'plank', source_id: 'e2e-source-01', name: '平板支撑', segment: { start_seconds: 0, end_seconds: 1.9 },
        parameters: { mode: 'duration', sets: 1, reps: null, duration_seconds: 2, rest_seconds: 0 },
        evidence: [{ type: 'visual', start_seconds: 0, end_seconds: 1.9 }], needs_confirmation: false },
    ],
  }
  const running = { ...result, status: 'running', stage: 'understanding', candidates: [], processed_seconds: 1 }
  await page.route('**/api/v1/analysis-runs', route => route.fulfill({ status: 202, json: running }))
  await page.route('**/api/v1/analysis-runs/sport-ai-audit', route => route.fulfill({ json: completed ? result : running }))
  await page.route('**/api/v1/analysis-runs/sport-ai-audit/events', async route => {
    await finished
    await route.fulfill({ contentType: 'text/event-stream', body: `event: run.completed\ndata: ${JSON.stringify({
      sequence: 1, type: 'run.completed', run_id: result.id,
      data: { stage: 'completed', source_duration_seconds: 2, processed_seconds: 2, discovered_candidate_count: 2, coverage_status: 'complete', coverage_gaps: [] },
    })}\n\n` })
  })
  await page.goto('/')
  await expect(page.locator('.file-picker input')).toBeEnabled()
  await page.locator('.file-picker input').setInputFiles(mediaPath)
  await expect(page.locator('.video-ticket video')).toBeVisible()
  await capture('01-video-input', true)
  await page.locator('.quick-real-section > summary').click()
  await capture('02-quick-start', true)
  await page.locator('.quick-video-branch > summary').click()
  await page.getByRole('button', { name: '0:02', exact: true }).click()
  await page.getByRole('button', { name: '确认并开始', exact: true }).click()
  await expect(page.locator('.progress-card')).toBeVisible()
  await capture('03-analysis-running', true)
  completed = true
  finishRun()
  await expect(page.locator('.completion-card')).toBeVisible()
  await capture('04-analysis-ready', true)
  await page.getByRole('button', { name: '查看训练方案' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(2)
  await page.locator('.action-summary').first().click()
  await page.getByLabel('每组次数', { exact: true }).fill('11')
  await page.locator('.action-summary').nth(1).click()
  await page.locator('.action-summary').first().click()
  await expect(page.getByLabel('每组次数', { exact: true })).toHaveValue('11')
  await expect(page.locator('.save-state')).toContainText('已自动保存到本机')
  await capture('05-plan-accordion', true)
  await page.getByRole('button', { name: '查看视频来源' }).click()
  await capture('07-source-detail', true)
  await page.getByRole('button', { name: '返回方案', exact: true }).click()

  // The manual field is protected. Use the preset's rule fields for editable AI review.
  await page.goto('/')
  page.once('dialog', dialog => dialog.accept())
  await page.locator('.quick-real-section > summary').click()
  await page.locator('.quick-preset').click()
  await expect(page).toHaveURL(/\/plan$/)
  await page.locator('[data-adjustment-trigger]').click()
  await page.getByLabel('训练经验', { exact: true }).selectOption('intermediate')
  await page.getByLabel('本次可用分钟', { exact: true }).fill('10')
  await page.locator('[data-adjustment-intent="more_challenging"]').click()
  await page.locator('[data-generate-adjustment]').click()
  await expect(page.locator('.review-summary')).toHaveCount(3)
  await page.locator('.review-summary').nth(1).click()
  await page.locator('.adjustment-dialog').getByLabel('每组次数', { exact: true }).fill('11')
  await page.locator('[data-preview-heading]').focus()
  await capture('06-ai-adjustment-preview', true)
  await page.locator('[data-apply-adjustment]').click()
  await expect(page.locator('.adjustment-dialog')).toBeHidden()
  await expect(page.locator('.action-summary').nth(1)).toContainText('11 次')
  await page.reload()
  await expect(page.locator('.action-summary').nth(1)).toContainText('11 次')
  await page.goto('/train')
  await expect(page.locator('.saved-row')).toHaveCount(2)
  await capture('08-training-library', true)
  page.once('dialog', dialog => dialog.accept())
  await page.locator('.saved-row').filter({ hasNotText: '8 分钟手臂唤醒' }).getByRole('button', { name: /打开/ }).click()
  await expect(page).toHaveURL(/\/plan$/)
  await page.getByRole('button', { name: '开始训练', exact: true }).click()
  await page.getByRole('button', { name: '准备好了', exact: true }).click()
  await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
  await capture('09-workout-active')
  await page.getByRole('button', { name: '暂停', exact: true }).click()
  await capture('11-workout-paused', true)
  await page.getByRole('button', { name: '继续训练', exact: true }).click()
  await page.getByRole('button', { name: '完成本组', exact: true }).click()
  await expect(page.locator('.status-pill')).toHaveText('组间休息')
  await capture('10-workout-rest')
  await page.getByRole('button', { name: '提前继续', exact: true }).click()
  await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '完成本组', exact: true }).click()
  await expect(page.locator('.status-pill')).toHaveText('组间休息')
  await expect(page.getByRole('button', { name: '准备好了', exact: true })).toBeVisible({ timeout: 18_000 })
  await expect(page.locator('.status-pill')).toHaveText('下一个动作')
  await capture('18-next-action', true)
  await page.getByRole('button', { name: '准备好了', exact: true }).click()
  await expect(page.locator('.status-pill')).toHaveText('准备开始')
  await capture('extra-countdown')
  await expect(page.locator('.status-pill')).toHaveText('倒计时进行中')
  await capture('extra-timed-set')
  await expect(page.getByRole('heading', { name: '训练完成', exact: true })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('link', { name: '查看训练结果', exact: true }).click()
  await expect(page).toHaveURL(/\/result\//, { timeout: 15_000 })
  await capture('12-training-result', true)
  const results = page.locator('.action-results')
  if (!await results.evaluate(element => element.hasAttribute('open'))) await results.locator('summary').first().click()
  await page.locator('.action-feedback').first().locator('summary').click()
  await page.getByRole('button', { name: '太累', exact: true }).first().click()
  await expect(page.locator('.action-feedback').first().locator('summary')).toHaveText('这项动作感觉如何 · 太累')
  await page.reload()
  if (!await results.evaluate(element => element.hasAttribute('open'))) await results.locator('summary').first().click()
  await expect(page.locator('.action-feedback').first().locator('summary')).toHaveText('这项动作感觉如何 · 太累')
  await page.goto('/mine')
  await capture('13-mine', true)
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations).toEqual([])
  await writeFile(path.join(output, 'coverage.json'), JSON.stringify({ provider: 'synthetic-e2e', captures }, null, 2))
})

test('direct editing keeps errors and controls reachable in a short mobile viewport with large text', async ({ page }) => {
  const output = path.resolve(process.cwd(), '../../tmp/audit/sport-ai-20261009')
  await mkdir(output, { recursive: true })
  await page.setViewportSize({ width: 320, height: 844 })
  await page.goto('/train')
  await page.getByRole('button', { name: '查看预设方案' }).click()
  await page.locator('.action-summary').first().click()
  await page.locator('[data-full-editor]').click()
  await page.getByLabel('动作名称', { exact: true }).fill('在家进行的站姿缓慢抬臂与肩部绕环准备动作')
  await page.getByRole('button', { name: '完成', exact: true }).click()
  await page.locator('.action-summary').first().click()
  const rest = page.getByLabel('休息秒数', { exact: true })
  await rest.fill('')
  await page.locator('.action-summary').nth(1).click()
  await expect(page.locator('.plan-card').first().locator('.card-validation')).toContainText('休息时间')
  await page.locator('.action-summary').first().click()
  await expect(rest).toHaveAttribute('aria-invalid', 'true')

  // Text enlargement and a reduced layout viewport exercise keyboard-space pressure.
  // This is browser emulation; it does not replace native soft-keyboard acceptance.
  await page.locator('.action-summary strong, .action-summary small, .numeric-field label, .numeric-field input').evaluateAll(elements => {
    for (const element of elements) (element as HTMLElement).style.fontSize = `${parseFloat(getComputedStyle(element).fontSize) * 1.5}px`
  })
  await page.setViewportSize({ width: 320, height: 420 })
  await rest.focus()
  await rest.fill('0')
  await rest.scrollIntoViewIfNeeded()
  await expect(rest).toBeInViewport({ ratio: 1 })
  await expect.poll(async () => {
    const fieldBox = (await rest.boundingBox())!
    const actionBox = (await page.locator('.start-training-panel').boundingBox())!
    return fieldBox.y + fieldBox.height <= actionBox.y
  }).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  for (const target of await page.locator('.inline-action-editor .numeric-stepper button, .inline-action-editor .numeric-stepper input').all()) {
    const box = (await target.boundingBox())!
    expect(box.height).toBeGreaterThanOrEqual(44)
    expect(box.width).toBeGreaterThanOrEqual(44)
  }
  await page.screenshot({ path: path.join(output, 'extra-small-viewport-320.png'), fullPage: false })
  await expect(page.locator('.save-state')).toContainText('已自动保存到本机')
  await page.reload()
  await page.locator('.action-summary').first().click()
  await expect(rest).toHaveValue('0')
  await writeFile(path.join(output, 'interaction.json'), JSON.stringify({
    width: 320, reducedHeight: 420, textScale: 1.5, checks: ['long action name', 'collapsed invalid state', 'focused input above fixed action', '44px numeric targets', 'zero rest persistence'],
  }, null, 2))
})
