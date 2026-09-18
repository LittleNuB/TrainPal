import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

const widths = [320, 390, 768, 1440] as const
const staticJourneyPages = [
  { path: '/train', heading: '训练' },
  { path: '/personalize', heading: /今天本来计划训练/ },
  { path: '/mine', heading: '我的' },
  { path: '/training', heading: '还没有未完成训练' },
  { path: '/result/not-found', heading: '没有找到这条训练记录' },
] as const

const expectNoHorizontalOverflow = async (page: Page) => {
  const hasOverflow = await page.evaluate(() => (
    document.documentElement.scrollWidth > document.documentElement.clientWidth
  ))
  expect(hasOverflow).toBe(false)
}

for (const width of widths) {
  test(`home journey remains usable without horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: /把想练的视频/ })).toBeVisible()
    await expect(page.getByText('选择健身视频', { exact: true })).toBeVisible()
    await expect(page.getByRole('navigation', { name: '主要导航' })).toBeVisible()

    await expectNoHorizontalOverflow(page)

    const importTarget = await page.locator('.file-picker').boundingBox()
    expect(importTarget?.height ?? 0).toBeGreaterThanOrEqual(44)

    for (const journey of staticJourneyPages) {
      await page.goto(journey.path)
      await expect(page.getByRole('heading', { level: 1, name: journey.heading })).toBeVisible()
      await expectNoHorizontalOverflow(page)
    }
  })
}

for (const width of [320, 390] as const) {
  test(`unconfirmed coach leaves the live training help usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/train')
    await page.getByRole('button', { name: '使用快速体验方案' }).click()

    await expect(page.getByRole('link', { name: 'GYMTI 与教练风格' })).toBeVisible()
    await expect(page.locator('.trainpal-coach')).toHaveCount(0)
    await page.getByRole('button', { name: '开始训练' }).click()

    await expect(page).toHaveURL(/\/training$/)
    await expect(page.getByRole('heading', { name: '肩部绕环' })).toBeVisible()
    await expect(page.getByText(/按自己的节奏来，训练进度会留在这里/)).toBeVisible()
    await expect(page.getByRole('button', { name: '准备好了' })).toBeVisible()
    await expect(page.locator('.trainpal-coach')).toHaveCount(0)
    await expectNoHorizontalOverflow(page)
  })
}

test('home and optional profile dialog pass the WCAG automated scan', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /把想练的视频/ })).toBeVisible()
  const home = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(home.violations).toEqual([])

  await page.goto('/mine')
  await page.getByRole('button', { name: /个人信息 · 可选/ }).click()
  await expect(page.getByRole('dialog', { name: '个人信息' })).toBeVisible()
  const profile = await new AxeBuilder({ page })
    .include('.detail-sheet')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(profile.violations).toEqual([])
})

test('an invalid plan name exposes an associated error and remains accessible', async ({ page }) => {
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  const name = page.getByLabel('方案名称')
  await name.fill('')
  await name.press('Tab')

  await expect(name).toHaveAttribute('aria-invalid', 'true')
  await expect(page.getByRole('alert').filter({ hasText: '方案名称不能为空' })).toBeVisible()
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(result.violations).toEqual([])
})

test('static journey states pass the WCAG scan and announce route changes', async ({ page }) => {
  for (const journey of staticJourneyPages) {
    await page.goto(journey.path)
    const heading = page.getByRole('heading', { level: 1, name: journey.heading })
    await expect(heading).toBeVisible()
    await expect(page).toHaveTitle(/TrainPal$/)
    await expect(heading).toBeFocused()
    await page.locator('main').evaluate(async (main) => {
      await Promise.all(main.getAnimations({ subtree: true }).map(async (animation) => {
        await animation.finished.catch(() => undefined)
      }))
    })
    const result = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    expect(result.violations).toEqual([])
  }
})
