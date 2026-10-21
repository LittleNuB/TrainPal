import { expect, test } from '@playwright/test'
import { fileURLToPath } from 'node:url'

for (const localFileName of ['e2e-source.mp4', 'e2e-landscape.mp4']) {
  const localVideoPath = fileURLToPath(new URL(`../../../../tmp/${localFileName}`, import.meta.url))
  test(`${localFileName} survives analysis, plan preview, and training recovery`, async ({ page }) => {
  await page.goto('/')

  const localFileInput = page.locator('.file-picker input[type="file"]')
  await expect(localFileInput).toBeEnabled()
  await localFileInput.setInputFiles(localVideoPath)
  await expect(page.getByText('本机视频', { exact: true })).toBeVisible()
  await expect(page.getByLabel('已选择的视频预览')).toHaveAttribute('src', /^blob:/)

  await page.getByRole('button', { name: '生成训练方案' }).click()
  await expect(page).toHaveURL(/\/analysis$/)
  await expect(page.getByRole('heading', { name: '训练方案已准备好' })).toBeVisible()
  await page.getByRole('button', { name: '查看训练方案' }).click()

  await expect(page).toHaveURL(/\/plan$/)
  const firstPlanCard = page.locator('.plan-card').first()
  await firstPlanCard.locator('.action-summary').click()
  await page.locator('[data-full-editor]').click()
  await expect(page.getByText(`本地视频 · ${localFileName}`, { exact: false })).toBeVisible()
  const confirmationSheet = page.locator('.action-sheet')
  await confirmationSheet.getByRole('button', { name: '确认并加入' }).click()
  await expect(confirmationSheet).toBeHidden()
  await firstPlanCard.locator('.action-summary').click()
  await page.locator('[data-full-editor]').click()
  await page.getByRole('button', { name: '预览来源视频' }).click()
  await expect(page.locator('.local-preview video')).toHaveAttribute('src', /^blob:/)

  await page.reload()
  await page.locator('.plan-card').first().locator('.action-summary').click()
  await page.locator('[data-full-editor]').click()
  await expect(page.getByText(`本地视频 · ${localFileName}`, { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '预览来源视频' }).click()
  await expect(page.locator('.local-preview video')).toHaveAttribute('src', /^blob:/)

  const sheet = page.locator('.action-sheet')
  await sheet.getByRole('spinbutton').nth(0).fill('1')
  await sheet.getByRole('spinbutton').nth(2).fill('0')
  await sheet.getByRole('button', { name: '完成', exact: true }).click()
  await page.getByRole('button', { name: '开始训练' }).click()

  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByRole('heading', { name: '拖拽弯举' })).toBeVisible()
  await expect(page.locator('.media-stage video')).toHaveAttribute('src', /^blob:/)
  const player = page.locator('.media-stage .video-player')
  await expect.poll(() => player.locator('video').evaluate(element => (element as HTMLVideoElement).videoWidth)).toBe(localFileName === 'e2e-source.mp4' ? 390 : 960)
  await expect(player).toHaveClass(localFileName === 'e2e-source.mp4' ? /video-player--portrait/ : /^(?!.*video-player--portrait).*$/)
  await page.getByRole('button', { name: '准备好了' }).click()
  await expect(page.getByText('本组进行中', { exact: true }).first()).toBeVisible()

  await page.reload()
  await expect(page.getByText(/已恢复并暂停|训练已暂停/, { exact: true }).first()).toBeVisible()
  await expect(page.locator('.media-stage video')).toHaveAttribute('src', /^blob:/)
  await page.getByRole('button', { name: '继续训练' }).click()
  await page.getByRole('button', { name: '完成本组' }).click()
  await expect(page.getByRole('heading', { name: '训练完成' })).toBeVisible()
})
}
