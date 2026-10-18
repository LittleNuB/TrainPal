import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

// Product screenshots use the built-in quick-start plan and a coach selected through the real UI.
// Speech events below exercise presentation, not real microphone recognition.
test('cat shortcut, focus and voice states stay usable with a confirmed coach', async ({ page }) => {
  test.setTimeout(120_000)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.addInitScript(() => {
    class Recognition {
      static available = async () => 'available'
      static install = async () => true
      get processLocally() { return true }
      set processLocally(_local: boolean) { /* test-only local recognition */ }
      onstart: (() => void) | null = null
      onresult: ((event: unknown) => void) | null = null
      receive = (event: Event) => this.onresult?.({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: (event as CustomEvent<string>).detail } }] })
      start() { window.addEventListener('test:voice', this.receive); queueMicrotask(() => this.onstart?.()) }
      abort() { window.removeEventListener('test:voice', this.receive) }
    }
    Object.defineProperty(window, 'SpeechRecognition', { value: Recognition, configurable: true })
  })
  const output = path.resolve(process.cwd(), '../../tmp/audit/voice-b-20261018')
  const readme = path.resolve(process.cwd(), '../../docs/readme')
  await mkdir(output, { recursive: true })
  const capture = async (name: string, publish = false, fullPage = false) => {
    await page.screenshot({ path: path.join(output, name + '.png'), fullPage })
    if (publish && process.env.TRAINPAL_CAPTURE_README === '1') {
      await mkdir(readme, { recursive: true })
      await page.screenshot({ path: path.join(readme, name + '.png'), fullPage })
    }
  }
  await page.goto('/personalize')
  for (let index = 0; index < 7; index++) {
    await page.locator('button[data-option-id$="_no_match"]').click()
    await expect(page.getByText('TrainPal 正在挑下一题…')).toBeHidden()
  }
  await page.locator('button[data-option-id]').first().click()
  await page.getByRole('button', { name: '跳过', exact: true }).click()
  await page.getByRole('button', { name: '修改风格' }).click()
  await page.locator('[data-style-id="hotblood"]').click()
  await page.locator('.style-action [data-action="confirm"]').click()
  await expect(page).toHaveURL(/\/plan$/)

  await page.setViewportSize({ width: 1440, height: 560 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '把视频整理成训练方案' })).toBeVisible()
  await capture('trainpal-home-20261018', true)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/train')
  await page.getByRole('button', { name: '查看预设方案' }).click()
  await expect(page.locator('.action-summary').first()).toBeVisible()
  await capture('trainpal-plan-20261018', true)
  await page.locator('.action-summary').first().click()
  await expect(page.locator('.numeric-stepper').first()).toBeVisible()
  await capture('trainpal-edit-20261018', true)
  await page.getByRole('button', { name: '开始训练', exact: true }).click()
  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByRole('button', { name: '跟我说', exact: true })).toBeVisible()
  await expect(page.locator('.voice-entry .trainpal-coach[data-style="hotblood"]')).toBeVisible()
  await expect(page.locator('.trainpal-coach:visible')).toHaveCount(1)
  await capture('trainpal-training-20261018', true)

  for (const [width, height] of [[320, 568], [390, 844], [768, 900], [1440, 900]] as const) {
    await page.setViewportSize({ width, height })
    await page.getByRole('button', { name: '跟我说', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('训练已暂停', { exact: true })).toBeVisible()
    await expect(page.locator('.trainpal-coach:visible')).toHaveCount(1)
    await page.getByRole('button', { name: '用文字', exact: true }).click()
    await expect(page.getByLabel('想让我做什么？')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const bounds = await dialog.boundingBox()
    expect(bounds!.width).toBeLessThanOrEqual(width)
    expect(bounds!.height).toBeLessThanOrEqual(height)
    const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    expect(accessibility.violations).toEqual([])
    await capture('voice-text-' + width)
    await page.keyboard.press('Escape')
    await expect(dialog).not.toBeVisible()
    await expect(page.getByRole('button', { name: '跟我说', exact: true })).toBeFocused()
  }

  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '语音唤醒 · 关', exact: true }).click()
  await page.getByRole('button', { name: '开启语音唤醒', exact: true }).click()
  await expect(page.getByText('喊「TrainPal」就能说')).toBeVisible()
  await page.getByRole('button', { name: '关闭 TrainPal', exact: true }).click()
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: 'TrainPal' })))
  await expect(page.getByRole('heading', { name: '我在听', exact: true })).toBeVisible()
  await capture('trainpal-voice-20261018', true)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('test:voice', { detail: '继续训练' })))
  await expect(page.getByRole('button', { name: '准备好了', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '语音唤醒 · 开', exact: true }).click()
  await expect(page.getByRole('button', { name: '语音唤醒 · 关', exact: true })).toBeVisible()
})
