import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

for (const width of [320, 390, 768, 1440]) {
  test(`watch workout preserves tips, previews and saves a clip, then auto advances at ${width}px`, async ({ page }) => {
    test.setTimeout(60_000)
    await page.setViewportSize({ width, height: 900 })
    // Browser speech events are synthetic; microphone/recognition quality is a separate device check.
    await page.addInitScript(() => {
      class TestRecognition {
        static available = async () => 'available'
        static install = async () => true
        get processLocally() { return true }
        set processLocally(_value: boolean) { /* local-only fixture */ }
        onstart: (() => void) | null = null
        onresult: ((event: unknown) => void) | null = null
        onend: (() => void) | null = null
        onerror: (() => void) | null = null
        receive = (event: Event) => this.onresult?.({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: (event as CustomEvent<string>).detail } }] })
        start() { window.addEventListener('test:voice', this.receive); queueMicrotask(() => this.onstart?.()) }
        abort() { window.removeEventListener('test:voice', this.receive) }
      }
      Object.defineProperty(window, 'SpeechRecognition', { value: TestRecognition, configurable: true })
    })
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
          parameters: { mode: 'reps', sets: 2, reps: 3, duration_seconds: null, rest_seconds: 4 },
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
    await page.route('**/api/v1/playback-adjustments', route => {
      const request = route.request().postDataJSON() as { options: { id: string; label: string }[] }
      return route.fulfill({ json: { option_id: request.options.find((option) => option.label === '推起时呼气')?.id } })
    })
    await page.goto('/')
    await expect(page).toHaveTitle(/TrainPal/)
    await page.locator('.quick-real-section > summary').click()
  await page.locator('.quick-video-branch > summary').click()
    await page.getByRole('button', { name: '0:02', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
    await page.getByRole('button', { name: '查看训练方案' }).click()
    await page.getByRole('button', { name: '开始训练', exact: true }).click()
    await expect(page).toHaveURL(/\/training$/)
    await page.getByRole('button', { name: '准备好了', exact: true }).click()
    await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
    await expect(page.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
    await page.getByRole('button', { name: '跟我说' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('dialog').getByText('训练已暂停', { exact: true })).toBeVisible()
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => document.querySelector('dialog')?.contains(document.activeElement))).toBe(true)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).not.toBeVisible()
    await expect(page.getByRole('button', { name: '跟我说' })).toBeFocused()
    await page.getByRole('button', { name: '跟我说' }).click()
    await page.getByRole('button', { name: '用文字', exact: true }).click()
    await page.getByLabel('想看哪段讲解？').fill('重看呼吸讲解')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.getByText('训练已暂停', { exact: true }).first()).toBeVisible()
    await page.getByRole('button', { name: '预览片段', exact: true }).click()
    await page.getByRole('button', { name: '用这段并继续', exact: true }).click()
    await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeVisible()
    await expect(page.getByText('已记住这个片段，下次复练也会使用。')).toBeVisible()
    await expect(page.getByRole('dialog')).not.toBeVisible()
    const output = path.join(tmpdir(), 'trainpal-playback-20260919')
    await mkdir(output, { recursive: true })
    await page.screenshot({ path: path.join(output, `${width}-training.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.getByRole('button', { name: '语音唤醒 · 关', exact: true }).click()
    await page.getByRole('button', { name: '开启语音唤醒', exact: true }).click()
    await expect(page.getByText('喊「TrainPal」就能说')).toBeVisible()
    await page.getByRole('button', { name: '关闭 TrainPal' }).click()
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，继续训练' })))
    await expect(page.getByRole('button', { name: '完成本组', exact: true })).toBeEnabled()
    await page.evaluate(() => {
      // A repeated recognition event from the same utterance must count only once.
      window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，完成本组' }))
      window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，完成本组' }))
    })
    await expect(page.getByText('本次已完成 1 / 3 组', { exact: true })).toBeVisible()
    await expect(page.locator('.status-pill')).toHaveText('组间休息')
    await expect(page.locator('dialog.voice-sheet')).toBeHidden()
    await expect(page.getByRole('button', { name: '语音唤醒 · 开', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '放大', exact: true }).click()
    const completionView = page.getByRole('dialog', { name: '当前动作示范 · 放大观看', exact: true })
    await expect.poll(() => completionView.locator('video').evaluate((video: HTMLVideoElement) => video.readyState >= 2 && video.duration > 0)).toBe(true)
    await page.screenshot({ path: path.join(output, `${width}-voice-completed-rest.png`) })
    await expect(completionView.getByRole('button', { name: '完成本组', exact: true })).toBeEnabled({ timeout: 10_000 })
    // The two-utterance wake flow must work too: name first, command afterwards.
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal' })))
    await expect(page.getByRole('heading', { name: '我在听', exact: true })).toBeVisible()
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: '完成本组' })))
    await expect(page.locator('dialog.voice-sheet')).toBeHidden()
    await expect(completionView).toContainText('本次已完成 2 / 3 组')
    await expect(completionView.getByRole('button', { name: '提前继续', exact: true })).toBeEnabled()
    await completionView.getByRole('button', { name: '收起视频', exact: true }).click()
    await expect(page.getByRole('button', { name: '准备好了', exact: true })).toBeVisible({ timeout: 7_000 })
    await expect(page.getByRole('heading', { name: '测试支撑', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '准备好了', exact: true }).click()
    await expect(page.getByRole('heading', { name: '训练完成', exact: true })).toBeVisible({ timeout: 7_000 })
    await page.getByRole('link', { name: '查看训练结果' }).click()
    await page.getByRole('button', { name: '再练一次' }).click()
    await page.reload()
    await page.getByRole('button', { name: '开始训练', exact: true }).click()
    await page.getByRole('button', { name: '跟我说' }).click()
    await page.getByRole('button', { name: '用文字', exact: true }).click()
    await page.getByText('片段操作', { exact: true }).click()
    await expect(page.getByRole('button', { name: '恢复完整教学', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '恢复完整教学', exact: true }).click()
    await expect(page.getByText('已恢复完整教学，准备好后继续。')).toBeVisible()
    await page.getByRole('button', { name: '开启语音唤醒', exact: true }).click()
    await page.getByRole('button', { name: '开启语音唤醒', exact: true }).click()
    await expect(page.getByText('喊「TrainPal」就能说')).toBeVisible()
    await page.getByRole('button', { name: '关闭 TrainPal' }).click()
    const video = await page.locator('video').elementHandle()
    await page.getByRole('button', { name: '放大', exact: true }).click()
    const expandedVideo = page.getByRole('dialog', { name: '当前动作示范 · 放大观看', exact: true })
    const voiceSheet = page.locator('dialog.voice-sheet')
    await expect(expandedVideo.getByRole('button', { name: '语音唤醒 · 开', exact: true })).toBeVisible()
    expect(await video!.evaluate(element => element === document.querySelector('.player-panel--expanded video'))).toBe(true)
    // Enable inside expanded mode as well as carrying an existing opt-in into it.
    await expandedVideo.getByRole('button', { name: '语音唤醒 · 开', exact: true }).click()
    await expandedVideo.getByRole('button', { name: '语音唤醒 · 关', exact: true }).click()
    await voiceSheet.getByRole('button', { name: '开启语音唤醒', exact: true }).click()
    await expect(voiceSheet.getByText('喊「TrainPal」就能说')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(voiceSheet).toBeHidden()
    await expect(expandedVideo).toBeVisible()
    await expect(expandedVideo.getByRole('button', { name: '跟我说', exact: true })).toBeFocused()
    // Closed nested dialogs must not break the expanded player's keyboard loop.
    await expandedVideo.getByRole('button', { name: '语音唤醒 · 开', exact: true }).focus()
    await page.keyboard.press('Tab')
    await expect(expandedVideo.getByRole('button', { name: '收起视频', exact: true })).toBeFocused()
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，继续训练' })))
    await expect(expandedVideo.getByRole('button', { name: '完成本组', exact: true })).toBeEnabled()
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，暂停训练' })))
    await expect(voiceSheet.getByText('训练已暂停', { exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(voiceSheet).toBeHidden()
    await expect(expandedVideo).toBeVisible()
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: '重看呼吸讲解' })))
    await expect(page.getByRole('button', { name: '用这段并继续', exact: true })).not.toBeVisible()
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，重看呼吸讲解' })))
    await expect(page.getByRole('button', { name: '用这段并继续', exact: true })).toBeVisible()
    await expect(page.getByText('也可以说“TrainPal，确认”')).toBeVisible()
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，预览' })))
    await expect(voiceSheet).toBeVisible()
    expect(await video!.evaluate(element => element === document.querySelector('.player-panel--expanded video'))).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await page.screenshot({ path: path.join(output, `${width}-voice-review.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，确认' })))
    await expect(page.getByText('已记住这个片段，下次复练也会使用。')).toBeVisible()
    await expect(voiceSheet).toBeHidden()
    await expect(expandedVideo.getByRole('button', { name: '完成本组', exact: true })).toBeEnabled()
    await expect(expandedVideo.getByRole('button', { name: '暂停', exact: true })).toBeVisible()
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await page.screenshot({ path: path.join(output, `${width}-expanded-voice.png`) })
    if (width === 390) {
      await page.setViewportSize({ width: 844, height: 390 })
      for (const name of ['跟我说', '语音唤醒 · 开', '完成本组', '暂停']) {
        await expect(expandedVideo.getByRole('button', { name, exact: true })).toBeInViewport()
      }
      await page.screenshot({ path: path.join(output, '844-expanded-voice.png') })
      await page.setViewportSize({ width, height: 900 })
    }
    await expandedVideo.getByRole('button', { name: '收起视频', exact: true }).click()
    await expect(page.getByRole('button', { name: '语音唤醒 · 开', exact: true })).toBeVisible()
    expect(await video!.evaluate(element => element === document.querySelector('video'))).toBe(true)
    if (width === 390) {
      await page.getByRole('button', { name: '放大', exact: true }).click()
      await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal，重看呼吸讲解' })))
      await expect(voiceSheet.getByRole('button', { name: '用这段并继续', exact: true })).toBeVisible()
      await video!.evaluate(element => element.dispatchEvent(new Event('error')))
      await expect(expandedVideo).toBeHidden()
      // A detached native dialog retains open but loses top-layer modality.
      // The same result sheet must recover after moving back into the page.
      await expect(voiceSheet.getByRole('button', { name: '用这段并继续', exact: true })).toBeVisible()
      await expect.poll(() => voiceSheet.evaluate(element => element.matches(':modal'))).toBe(true)
      await page.keyboard.press('Tab')
      expect(await voiceSheet.evaluate(element => element.contains(document.activeElement))).toBe(true)
      await page.keyboard.press('Escape')
      await expect(voiceSheet).toBeHidden()
      await expect(page.getByRole('button', { name: '跟我说', exact: true })).toBeFocused()
    }
    await page.getByRole('button', { name: '语音唤醒 · 开', exact: true }).click()
    await expect(page.getByText('喊「TrainPal」就能说')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}
