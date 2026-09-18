import { expect, test } from '@playwright/test'

const expectedDurations = ['0:02', '0:03', '0:04', '0:05', '0:06']

test('home presents exactly five duration-only real-analysis sources in ascending order', async ({ page }) => {
  await page.goto('/')
  await page.locator('.quick-real-section summary').click()
  await expect(page.getByRole('heading', { name: /把想练的视频/ })).toBeVisible()

  const sources = page.locator('.quick-real-source')
  await expect(sources).toHaveText(expectedDurations)
  await expect(page.getByText(/Private E2E source/)).toHaveCount(0)
  await expect(page.getByText('评委入口', { exact: true })).toHaveCount(0)
  await expect(page.getByText('体验码', { exact: true })).toHaveCount(0)
})

test('real analysis starts once only after the visitor confirms', async ({ page }) => {
  let createRequests = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST'
      && new URL(request.url()).pathname === '/api/v1/analysis-runs'
    ) {
      createRequests += 1
    }
  })

  await page.goto('/')
  await page.locator('.quick-real-section summary').click()
  const firstSource = page.getByRole('button', { name: expectedDurations[0], exact: true })
  await firstSource.click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  expect(createRequests).toBe(0)

  await dialog.getByRole('button', { name: '取消' }).click()
  await expect(dialog).toBeHidden()
  expect(createRequests).toBe(0)

  await firstSource.click()
  await dialog.getByRole('button', { name: '确认并开始' }).click()
  await expect(page).toHaveURL(/\/analysis$/)
  await expect.poll(() => createRequests).toBe(1)
})
