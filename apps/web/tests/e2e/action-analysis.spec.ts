import { expect, test } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const controlledMediaRoot = fileURLToPath(
  new URL('../../../../tmp/e2e-sources/', import.meta.url),
)

test('video result becomes a base plan, resolves uncertainty, and restores from the current plan', async ({ page }) => {
  await page.route('**/api/v1/sources/*/media', async (route) => {
    const sourceId = new URL(route.request().url()).pathname.split('/').at(-2) ?? ''
    expect(sourceId).toMatch(/^e2e-source-\d{2}$/)
    await route.fulfill({
      path: path.join(controlledMediaRoot, `${sourceId}.mp4`),
      contentType: 'video/mp4',
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: /把想练的视频/ })).toBeVisible()

  const primaryNavigation = page.getByRole('navigation', { name: '主要导航' })
  for (const target of ['首页', '训练', '我的'].map((name) => (
    primaryNavigation.getByRole('link', { name, exact: true })
  ))) {
    const box = await target.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.width).toBeGreaterThanOrEqual(44)
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }

  await page.locator('.quick-real-section summary').click()
  await page.getByRole('button', { name: '0:02', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
  await expect(page).toHaveURL(/\/analysis$/)

  await expect(page.getByRole('heading', { name: '训练方案已准备好' })).toBeVisible()
  const preparedPlanAction = page.getByRole('button', { name: '查看训练方案' })
  const preparedPlanActionBox = await preparedPlanAction.boundingBox()
  expect(preparedPlanActionBox).not.toBeNull()
  expect(preparedPlanActionBox!.height).toBeGreaterThanOrEqual(44)
  await preparedPlanAction.click()

  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.getByRole('heading', { name: '当前训练方案' })).toBeVisible()
  await expect(page.getByText('待确认', { exact: true })).toBeVisible()
  await page.locator('.plan-card').first().locator('.action-summary').click()
  await expect(
    page.locator('.action-sheet').getByRole('textbox', { name: '动作名称' }),
  ).toHaveValue('拖拽弯举')
  await expect(page.locator('.action-sheet').getByText('规则补全', { exact: true }).first()).toBeVisible()
  const firstConfirmationSheet = page.locator('.action-sheet')
  await firstConfirmationSheet.getByRole('button', { name: '确认并加入' }).click()
  await expect(firstConfirmationSheet).toBeHidden()

  await page.getByRole('link', { name: '返回训练' }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '首页', exact: true }).click()
  await page.locator('.quick-real-section summary').click()
  await page.getByRole('button', { name: '0:03', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
  await expect(page).toHaveURL(/\/analysis$/)
  await expect(page.getByRole('heading', { name: '训练方案已准备好' })).toBeVisible()
  await page.getByRole('button', { name: '查看训练方案' }).click()
  await expect(page.getByRole('dialog', { name: '当前已经有训练方案' })).toBeVisible()
  await page.getByRole('button', { name: /追加/ }).click()
  await expect(page.locator('.plan-card')).toHaveCount(2)

  await page.locator('.plan-card').nth(1).locator('.action-summary').click()
  const secondConfirmationSheet = page.locator('.action-sheet')
  await secondConfirmationSheet.getByRole('button', { name: '确认并加入' }).click()
  await expect(secondConfirmationSheet).toBeHidden()

  await page.reload()
  await expect(page.locator('.plan-card')).toHaveCount(2)

  await page.getByRole('button', { name: /创建动作/ }).click()
  const manualForm = page.locator('.manual-form')
  await manualForm.getByLabel('动作名称').fill('平板支撑')
  await manualForm.getByRole('button', { name: '按时长' }).click()
  await manualForm.getByRole('button', { name: '加入方案' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(3)
  await expect(page.locator('.plan-card').last()).toContainText('平板支撑')

  for (const [index, target] of [
    [0, '10'],
    [1, '10'],
    [2, '1'],
  ] as const) {
    await page.locator('.plan-card').nth(index).locator('.action-summary').click()
    const sheet = page.locator('.action-sheet')
    await sheet.getByRole('spinbutton').nth(0).fill('1')
    await sheet.getByRole('spinbutton').nth(1).fill(target)
    await sheet.getByRole('spinbutton').nth(2).fill('0')
    await sheet.getByRole('button', { name: '完成', exact: true }).click()
    await expect(sheet).toBeHidden()
  }

  await page.getByRole('button', { name: '开始训练' }).click()
  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByRole('heading', { name: '拖拽弯举' })).toBeVisible()
  await expect(page.locator('.media-stage video')).toBeVisible()

  await page.getByRole('button', { name: '准备好了' }).click()
  await expect(page.getByText('本组进行中', { exact: true }).first()).toBeVisible()
  await page.reload()
  await expect(page.getByText(/已恢复并暂停|训练已暂停/, { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '继续训练' }).click()
  await page.getByRole('button', { name: '完成本组' }).click()

  await expect(page.getByText('准备继续', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '准备好了' }).click()
  await expect(page.getByRole('heading', { name: '拖拽弯举' })).toBeVisible()
  await expect(page.locator('.media-stage video')).toBeVisible()
  await page.getByRole('button', { name: '完成本组' }).click()

  await expect(page.getByText('准备继续', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '准备好了' }).click()
  await expect(page.getByRole('heading', { name: '平板支撑' })).toBeVisible()
  await expect(page.getByText('这个动作没有参考视频')).toBeVisible()
  await expect(page.getByRole('heading', { name: '训练完成' })).toBeVisible({ timeout: 5_000 })
  await expect(page.getByText(/实际完成量已保存到本机/)).toBeVisible()
  await page.goto('/train')
  await expect(page.locator('.saved-row')).toHaveCount(1)
  await expect(page.locator('.saved-row')).toContainText('3 个动作')
})
