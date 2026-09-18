import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

test('completed result keeps mobile details optional and uses the desktop summary layout', async ({ page }) => {
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  for (let index = 0; index < 3; index++) {
    await page.locator('.plan-card .action-summary').nth(index).click()
    const editor = page.getByRole('dialog', { name: '调整动作' })
    await editor.getByRole('button', { name: '按次数', exact: true }).click()
    await editor.getByRole('spinbutton').nth(0).fill('1')
    await editor.getByRole('spinbutton').nth(1).fill('1')
    await editor.getByRole('spinbutton').nth(2).fill('0')
    await editor.getByRole('button', { name: '完成', exact: true }).click()
  }
  await page.getByRole('button', { name: '开始训练', exact: true }).click()
  await page.getByRole('button', { name: '准备好了', exact: true }).click()
  for (let index = 0; index < 3; index++) {
    if (index) await page.getByRole('button', { name: '准备好了', exact: true }).click()
    await page.getByRole('button', { name: '完成本组', exact: true }).click()
  }
  await page.getByRole('link', { name: '查看训练结果' }).click()
  const details = page.locator('.action-results')
  await expect(page.getByRole('heading', { name: '练完啦', exact: true })).toBeVisible()
  await expect(details).not.toHaveAttribute('open')
  await details.locator('summary').click()
  await expect(details).toHaveAttribute('open')
  await details.locator('summary').click()
  await expect(details).not.toHaveAttribute('open')

  const output = path.resolve(process.cwd(), '../../tmp/design-c-implemented')
  await mkdir(output, { recursive: true })
  await page.screenshot({ path: path.join(output, '390-result.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect(details).toHaveAttribute('open')
  await expect(page.locator('.action-result-row')).toHaveCount(3)
  await page.screenshot({ path: path.join(output, '1440-result.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(details).not.toHaveAttribute('open')
  await expect(page.getByRole('button', { name: '再练一次', exact: true })).toBeVisible()
})
