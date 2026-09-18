import { expect, test } from '@playwright/test'

test('visitor can install the labelled quick plan from the training hub', async ({ page }) => {
  await page.goto('/train')

  await expect(page.getByRole('heading', { name: '训练', exact: true })).toBeVisible()
  await expect(page.getByText(/快速体验方案/).first()).toBeVisible()
  await page.getByRole('button', { name: '使用快速体验方案' }).click()

  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.getByLabel('方案名称')).toHaveValue('8 分钟手臂唤醒')
  await expect(page.getByText('这些示例不是 AI 分析结果', { exact: false })).toBeVisible()

  const firstAction = page.locator('.plan-card').first()
  const summaryControls = [
    firstAction.locator('.action-summary'),
  ]
  for (const control of summaryControls) {
    await control.scrollIntoViewIfNeeded()
    const box = await control.boundingBox()
    expect(box, `${await control.getAttribute('aria-label') ?? await control.textContent()} 应可单手点按`).not.toBeNull()
    expect(box!.width).toBeGreaterThanOrEqual(44)
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }

  await firstAction.locator('.action-summary').click()
  const sheet = page.locator('.action-sheet')
  const detailControls = [
    sheet.getByRole('button', { name: '上移' }),
    sheet.getByRole('button', { name: '下移' }),
    sheet.getByRole('button', { name: '按次数' }),
    sheet.getByRole('button', { name: '按时长' }),
    sheet.getByRole('button', { name: '复制动作' }),
    sheet.getByRole('button', { name: '删除动作' }),
    sheet.getByRole('button', { name: '完成', exact: true }),
  ]
  for (const control of detailControls) {
    await control.scrollIntoViewIfNeeded()
    const box = await control.boundingBox()
    expect(box, `${await control.getAttribute('aria-label') ?? await control.textContent()} 应可单手点按`).not.toBeNull()
    expect(box!.width).toBeGreaterThanOrEqual(44)
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }
})
