import { expect, test } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const media = (name: string) => fileURLToPath(new URL(`../../../../tmp/${name}.mp4`, import.meta.url))

for (const width of [320, 390, 768, 1440]) {
  test(`video keeps its orientation with usable controls at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/')
    const input = page.locator('.file-picker input')
    await expect(input).toBeEnabled()
    await input.setInputFiles(media('e2e-source'))
    const player = page.locator('.video-ticket .video-player')
    await expect(player).toHaveClass(/video-player--portrait/)
    const canvas = player.locator('.video-canvas')
    let bounds = await canvas.boundingBox()
    expect(bounds!.height).toBeGreaterThan(400)
    const controls = await player.locator('.player-controls').boundingBox()
    expect(controls!.y).toBeGreaterThanOrEqual(bounds!.y + bounds!.height - 1)
    expect(await player.locator('video').evaluate(video => getComputedStyle(video).objectFit)).toBe('contain')
    await player.getByRole('button', { name: '播放视频', exact: true }).click()
    await expect(player.getByRole('button', { name: '暂停视频', exact: true })).toBeVisible()
    await player.getByRole('button', { name: '放大', exact: true }).click()
    const expanded = page.getByRole('dialog', { name: /放大观看/ })
    await expect(expanded).toBeVisible()
    expect((await expanded.boundingBox())!.height).toBe(900)
    await page.keyboard.press('Escape')
    await expect(expanded).toBeHidden()
    await expect(player.getByRole('button', { name: '放大', exact: true })).toBeFocused()
    await input.setInputFiles(media('e2e-landscape'))
    await expect(player).not.toHaveClass(/video-player--portrait/)
    await expect.poll(() => player.locator('video').evaluate(video => (video as HTMLVideoElement).videoWidth)).toBe(960)
    bounds = await canvas.boundingBox()
    expect(bounds!.width / bounds!.height).toBeCloseTo(16 / 9, 1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}
