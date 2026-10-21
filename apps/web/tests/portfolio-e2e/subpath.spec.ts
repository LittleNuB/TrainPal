import { expect, test } from '@playwright/test'
import { fileURLToPath } from 'node:url'

test('subpath keeps uploads, SSE, SPA refresh and restored local video inside TrainPal', async ({ page }) => {
  const apiPaths: string[] = []
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (path.includes('/api/v1/')) apiPaths.push(path)
  })
  await page.goto('/trainpal/')
  await page.locator('.file-picker input[type="file"]').setInputFiles(
    fileURLToPath(new URL('../../../../tmp/e2e-source.mp4', import.meta.url)),
  )
  await page.getByRole('button', { name: '生成训练方案', exact: true }).click()
  await expect(page).toHaveURL(/\/trainpal\/analysis$/)
  await expect(page.getByRole('heading', { name: '训练方案已准备好' })).toBeVisible()
  await page.getByRole('button', { name: '查看训练方案', exact: true }).click()
  await expect(page).toHaveURL(/\/trainpal\/plan$/)
  await page.reload()
  await expect(page.locator('.action-summary').filter({ hasText: '拖拽弯举' })).toBeVisible()
  await expect(page.getByRole('link', { name: '返回训练', exact: true })).toHaveAttribute('href', '/trainpal/train')
  expect(apiPaths.every(path => path.startsWith('/trainpal/api/'))).toBe(true)
  expect(apiPaths.some(path => path.endsWith('/events'))).toBe(true)
  expect(apiPaths.some(path => path.endsWith('/analysis-runs/local'))).toBe(true)
})


test('GYMTI result and cat animation decode under the portfolio mount', async ({ page }) => {
  await page.goto('/trainpal/personalize')
  for (let i = 0; i < 7; i++) {
    await page.locator('button[data-option-id$="_no_match"]').click()
    await expect(page.getByText('TrainPal 正在挑下一题…')).toBeHidden()
  }
  await expect(page.getByText('最后一题', { exact: true })).toBeVisible()
  await page.locator('button[data-option-id]').first().click()
  await page.getByRole('button', { name: '跳过', exact: true }).click()
  const art = page.locator('.gymti-art img')
  await expect(art).toHaveAttribute('src', /^\/trainpal\/gymti\/types\//)
  await expect.poll(() => art.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
  const coach = page.locator('.coach-result .coach-motion__image')
  await expect(coach).toHaveAttribute('src', /^\/trainpal\/trainpal\/pets\//)
  await expect.poll(() => coach.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
})
