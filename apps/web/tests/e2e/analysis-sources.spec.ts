import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { fingerprintTrainingReminderInput } from '../../src/api/reminder-fingerprint'

test('five source tips and bound reminders survive editing, copying, decoded playback and record repractice', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  // All analysis and reminder content below is a synthetic HTTP fixture, not real AI evidence.
  let reminderRequests = 0
  const plannedIds: string[] = []
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const media = await readFile(fileURLToPath(new URL('../../../../tmp/e2e-sources/e2e-source-01.mp4', import.meta.url)))
  await page.route('**/api/v1/sources/*/media', async route => {
    const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/)
    const start = range ? Number(range[1]) : 0
    const end = range?.[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1
    await route.fulfill({ status: range ? 206 : 200, contentType: 'video/mp4', body: media.subarray(start, end + 1),
      headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${media.length}` } : {}) },
    })
  })
  await page.route('**/api/v1/training-reminders', async route => {
    reminderRequests += 1
    const input = route.request().postDataJSON()
    plannedIds.push(input.plan_id)
    const action = input.actions[0]
    expect(action.tips).toHaveLength(5)
    await route.fulfill({ json: {
      plan_id: input.plan_id, plan_revision: input.plan_revision,
      input_fingerprint: await fingerprintTrainingReminderInput(input), contract_version: 'training-reminders.v1.1',
      skill_version: '1.0.0', policy_version: 'ReminderPolicy.v1', mode: 'rules', fallback_reason: 'model_unavailable',
      reminders: [{ action_id: action.action_id, action_revision: action.action_revision, tip_id: action.tips[0].tip_id,
        stage: 'training', trigger: 'set_start', set_number: 1, active_seconds: null, priority: 1, max_plays: 1 }],
      budgets: [{ action_id: action.action_id, utterance_limit: 1, active_interval_seconds: 30, end_quiet_seconds: 10 }],
    } })
  })
  const result = {
    id: 'sources-fixture', source_id: 'e2e-source-01', trigger_seconds: null,
    status: 'completed', stage: 'completed', warnings: [],
    empty_reason: null, error: null, source_duration_seconds: 2, processed_seconds: 2,
    discovered_candidate_count: 1, coverage_status: 'complete', coverage_gaps: [],
    created_at: '2026-09-19T00:00:00Z', updated_at: '2026-09-19T00:00:01Z',
    candidates: [{
      id: 'candidate-1', source_id: 'e2e-source-01', name: '标准俯卧撑',
      segment: { start_seconds: 0.2, end_seconds: 1.8 },
      parameters: { mode: 'reps', sets: null, reps: 12, reps_max: 15, duration_seconds: null, rest_seconds: 45 },
      evidence: [{ type: 'speech', start_seconds: 0.3, end_seconds: 1.7 },
        { type: 'visual', start_seconds: 0.2, end_seconds: 1.8 }],
      tips: [
        { text: '推起时呼气', category: 'breathing', evidence: { type: 'speech', start_seconds: 0.3, end_seconds: 1.7 } },
        { text: '先保持支撑稳定', category: 'setup', evidence: { type: 'visual', start_seconds: 0.2, end_seconds: 0.5 } },
        { text: '缓慢下降', category: 'path', evidence: { type: 'visual', start_seconds: 0.5, end_seconds: 0.8 } },
        { text: '不要突然加速', category: 'caution', evidence: { type: 'visual', start_seconds: 0.8, end_seconds: 1.1 } },
        { text: '起身保持节奏', category: 'rhythm', evidence: { type: 'visual', start_seconds: 1.1, end_seconds: 1.4 } },
      ],
      parameter_conflicts: [{ field: 'sets', alternatives: [
        { parameters: { sets: 3 }, evidence: [{ type: 'speech', start_seconds: 0.3, end_seconds: 1.7 }] },
        { parameters: { sets: 4 }, evidence: [{ type: 'visual', start_seconds: 0.2, end_seconds: 1.8 }] },
      ] }],
      needs_confirmation: true,
    }],
  }
  await page.route('**/api/v1/analysis-runs', route => route.fulfill({ status: 202,
    json: { ...result, status: 'queued', stage: 'queued', candidates: [] } }))
  await page.route('**/api/v1/analysis-runs/sources-fixture', route => route.fulfill({ json: result }))
  await page.route('**/api/v1/analysis-runs/sources-fixture/events', route => route.fulfill({
    contentType: 'text/event-stream', body: `event: run.completed\ndata: ${JSON.stringify({
      sequence: 1, type: 'run.completed', run_id: result.id,
      data: { stage: 'completed', source_duration_seconds: 2, processed_seconds: 2,
        discovered_candidate_count: 1, coverage_status: 'complete', coverage_gaps: [] },
    })}\n\n`,
  }))
  await page.goto('/')
  await page.locator('.quick-real-section > summary').click()
  await page.locator('.quick-video-branch > summary').click()
  await page.getByRole('button', { name: '0:02', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
  await page.getByRole('button', { name: '查看训练方案' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(1)
  await page.locator('.action-summary').click()
  await page.locator('[data-full-editor]').click()
  const sheet = page.locator('.action-sheet')
  await expect(sheet.getByLabel('动作名称', { exact: true })).toHaveValue('标准俯卧撑')
  await expect(sheet.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
  await sheet.getByText('查看全部要点与出处', { exact: true }).click()
  await expect(sheet.locator('.source-tips li')).toHaveCount(5)
  for (const checkbox of await sheet.getByRole('checkbox').all()) await expect(checkbox).toBeDisabled()
  await expect(sheet.getByText('起身保持节奏', { exact: true })).toBeVisible()
  await expect(sheet.getByText('视频中有不同说法，请核对后设置')).toBeVisible()
  await expect(sheet.locator('.source-parameter-note li').nth(0)).toContainText('3 组')
  await expect(sheet.locator('.source-parameter-note li').nth(1)).toContainText('4 组')
  await expect(sheet.getByText('视频建议：每组 12～15 次')).toBeVisible()
  await sheet.getByRole('spinbutton').nth(1).fill('10')
  await sheet.getByRole('spinbutton').nth(0).fill('1')
  await sheet.getByRole('spinbutton').nth(2).fill('0')
  await sheet.getByRole('button', { name: '确认并加入' }).click()
  await page.getByRole('link', { name: '返回训练', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '训练', exact: true }).click()
  await expect(page.locator('.saved-row')).toHaveCount(1)
  page.once('dialog', dialog => dialog.accept())
  await page.locator('.saved-row').getByRole('button', { name: '打开' }).click()
  await expect(page).toHaveURL(/\/plan$/)
  await page.reload()
  await page.locator('.action-summary').click()
  await page.locator('[data-full-editor]').click()
  await expect(sheet.getByRole('spinbutton').nth(1)).toHaveValue('10')
  await expect(sheet.getByText('你的调整', { exact: true }).first()).toBeVisible()
  await expect(sheet.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
  await sheet.getByText('查看全部要点与出处', { exact: true }).click()
  await expect(sheet.locator('.source-tips li')).toHaveCount(5)
  await expect(sheet.getByText('起身保持节奏', { exact: true })).toBeVisible()
  await expect(sheet.locator('.source-parameter-note li')).toHaveCount(2)
  for (const checkbox of await sheet.getByRole('checkbox').all()) {
    await expect(checkbox).not.toBeChecked()
    await checkbox.check()
  }
  await sheet.getByRole('button', { name: '完成', exact: true }).click()
  await page.locator('.extra-settings > summary').click()
  await page.getByRole('checkbox', { name: '训练时听动作要点', exact: true }).check()
  await expect(page.getByText('提醒安排已保存', { exact: false })).toBeVisible()
  await page.reload()
  await page.locator('.extra-settings > summary').click()
  await expect(page.getByRole('checkbox', { name: '训练时听动作要点', exact: true })).toBeChecked()
  await expect(page.getByText('提醒安排已保存', { exact: false })).toBeVisible()
  expect(reminderRequests).toBe(1)
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`reminder-plan-${width}.png`), fullPage: true })
    await page.locator('.action-summary').click()
    await page.locator('[data-full-editor]').click()
    await sheet.getByText('查看全部要点与出处', { exact: true }).click()
    for (const checkbox of await sheet.getByRole('checkbox').all()) await expect(checkbox).toBeChecked()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    // Axe must measure the final colors, not the translucent entrance frame.
    // Keep motion enabled and wait for real animations instead of a fixed sleep.
    await sheet.evaluate(async element => {
      await Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished))
    })
    const accessibility = await new AxeBuilder({ page }).include('.action-sheet').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    expect(accessibility.violations).toEqual([])
    await page.screenshot({ path: testInfo.outputPath(`reminder-review-${width}.png`), fullPage: true })
    await sheet.getByRole('button', { name: '完成', exact: true }).click()
  }
  expect(reminderRequests).toBe(1)
  await page.setViewportSize({ width: 390, height: 900 })
  const originalName = await page.getByRole('textbox', { name: '方案名称', exact: true }).inputValue()
  await page.getByRole('button', { name: '复制方案', exact: true }).click()
  await page.getByRole('textbox', { name: '新方案名称', exact: true }).fill('合成五要点复练副本')
  await page.getByRole('button', { name: '创建副本', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '方案名称', exact: true })).toHaveValue('合成五要点复练副本')
  await expect(page.getByText('提醒安排已保存', { exact: false })).toBeVisible()
  expect(reminderRequests).toBe(2)
  expect(plannedIds[1]).not.toBe(plannedIds[0])
  await page.reload()
  await page.locator('.extra-settings > summary').click()
  await expect(page.getByText('提醒安排已保存', { exact: false })).toBeVisible()
  expect(reminderRequests).toBe(2)

  const expectDecodedVideo = async (): Promise<void> => {
    const video = page.locator('.media-stage video')
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) => ({
      decoded: element.readyState >= 2,
      width: element.videoWidth, height: element.videoHeight,
      frames: element.getVideoPlaybackQuality().totalVideoFrames > 0,
      paused: element.paused, time: element.currentTime, seeking: element.seeking, error: element.error?.code ?? null,
    }))).toMatchObject({ decoded: true, width: 390, height: 694, frames: true, paused: false, seeking: false, error: null })
    const pixel = await video.evaluate((element: HTMLVideoElement) => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 1
      const context = canvas.getContext('2d')!
      context.drawImage(element, 0, 0, 1, 1)
      return Array.from(context.getImageData(0, 0, 1, 1).data)
    })
    // The generated fixture is a solid 0x101820 frame, not a mocked video element.
    expect(pixel[0]).toBeGreaterThan(8)
    expect(pixel[1]).toBeGreaterThan(pixel[0]!)
    expect(pixel[2]).toBeGreaterThan(pixel[1]!)
    expect(pixel[3]).toBe(255)
  }
  await page.getByRole('button', { name: '开始训练', exact: true }).click()
  await page.getByRole('button', { name: '准备好了', exact: true }).click()
  await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
  await expectDecodedVideo()
  await expect(page.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '完成本组', exact: true }).click()
  await expect(page.getByRole('heading', { name: '训练完成', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '查看训练结果', exact: true }).click()
  await expect(page).toHaveURL(/\/result\/[^/]+$/)
  const resultUrl = page.url()
  await expect(page.getByRole('heading', { name: '合成五要点复练副本', exact: true })).toBeVisible()

  // Force record restoration instead of the same-current-plan shortcut.
  await page.goto('/train')
  await expect(page.locator('.saved-row').filter({ hasText: originalName })).toHaveCount(1)
  await expect(page.locator('.saved-row').filter({ hasText: '合成五要点复练副本' })).toHaveCount(1)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '新建方案', exact: true }).click()
  // The hub has no plan cards either; wait for creation/navigation before leaving.
  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.locator('.plan-card')).toHaveCount(0)
  await page.goto(resultUrl)
  await page.getByRole('button', { name: '再练一次', exact: true }).click()
  await expect(page).toHaveURL(/\/plan$/)
  await page.locator('.extra-settings > summary').click()
  await expect(page.getByText('提醒安排已保存', { exact: false })).toBeVisible()
  expect(reminderRequests).toBe(3)
  expect(new Set(plannedIds).size).toBe(3)
  await page.reload()
  await page.locator('.extra-settings > summary').click()
  await expect(page.getByText('提醒安排已保存', { exact: false })).toBeVisible()
  expect(reminderRequests).toBe(3)
  await page.locator('.action-summary').click()
  await page.locator('[data-full-editor]').click()
  await expect(sheet.getByRole('spinbutton').nth(0)).toHaveValue('1')
  await expect(sheet.getByRole('spinbutton').nth(1)).toHaveValue('10')
  await expect(sheet.getByRole('spinbutton').nth(2)).toHaveValue('0')
  await sheet.getByText('查看全部要点与出处', { exact: true }).click()
  await expect(sheet.locator('.source-tips li')).toHaveCount(5)
  for (const checkbox of await sheet.getByRole('checkbox').all()) await expect(checkbox).toBeChecked()
  await expect(sheet.locator('.source-parameter-note li')).toHaveCount(2)
  await sheet.getByRole('button', { name: '完成', exact: true }).click()
  await page.getByRole('button', { name: '开始训练', exact: true }).click()
  await page.getByRole('button', { name: '准备好了', exact: true }).click()
  await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
  await expectDecodedVideo()
  expect(reminderRequests).toBe(3)
  expect(errors).toEqual([])
})
