import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

for (const width of [320, 390, 768, 1440]) {
test(`TrainPal previews and applies confirmed changes at ${width}px`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width, height: 900 })
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await expect(page).toHaveURL(/\/plan$/)

  await page.locator('button[data-adjustment-trigger]').click()
  const dialog = page.getByRole('dialog', { name: '调整这次训练' })
  await expect(dialog).toBeVisible()
  await dialog.locator('button[data-adjustment-intent="more_challenging"]').click()
  await dialog.locator('button[data-generate-adjustment]').click()

  await expect(dialog.locator('.adjustment-change-list')).toBeVisible()
  await expect(dialog.getByText(/→/).first()).toBeVisible()
  const accessibility = await new AxeBuilder({ page })
    .include('.adjustment-dialog')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(accessibility.violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('adjustment-preview.png'), fullPage: true })
  await dialog.locator('button[data-apply-adjustment]').click()

  await expect(page.getByRole('heading', { name: '已为本次训练调整' })).toBeVisible()
  await expect(page.locator('.action-summary').first()).toContainText('每组 35 秒')
  await page.reload()
  await expect(page.getByRole('heading', { name: '已为本次训练调整' })).toBeVisible()
  await expect(page.locator('.action-summary').first()).toContainText('每组 35 秒')
  await page.locator('button[data-restore-base-plan]').click()
  await expect(page.getByRole('heading', { name: '需要更贴近你现在的状态？' })).toBeVisible()
  await expect(page.locator('.action-summary').first()).toContainText('每组 30 秒')
})
}
