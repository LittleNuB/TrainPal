import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

const output = path.resolve(process.cwd(), '../../tmp/audit/redesign')
const installPlan = async (page: Page) => {
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await expect(page).toHaveURL(/\/plan$/)
}

// An explicit, synthetic confirmed preference in an isolated test browser only.
const confirmTestCoach = async (page: Page) => {
  await page.evaluate(async () => {
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open('hachimi-fitness')
      request.onerror = () => reject(request.error)
      request.onsuccess = () => {
        const db = request.result
        const tx = db.transaction('preferences', 'readwrite')
        tx.objectStore('preferences').put({ id: 'current', petVisible: true, coachStyleId: 'gentle', updatedAt: new Date().toISOString() })
        tx.oncomplete = () => { db.close(); resolve() }
        tx.onerror = () => { db.close(); reject(tx.error) }
      }
    })
  })
  await page.reload()
  await expect(page.locator('.floating-coach')).toBeVisible()
}

const expectSafeCoach = async (page: Page) => {
  await expect.poll(() => page.locator('.floating-coach').evaluate((coach) => {
    const rect = coach.getBoundingClientRect()
    const obstacles = [...document.querySelectorAll('[data-coach-avoid], .global-task-dock')]
      .filter((element) => element.getClientRects().length)
      .map((element) => element.getBoundingClientRect())
    return rect.left >= 0 && rect.top >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight
      && obstacles.every((other) => rect.right <= other.left || rect.left >= other.right || rect.bottom <= other.top || rect.top >= other.bottom)
  })).toBe(true)
}

for (const width of [320, 390, 768, 1440]) {
  test(`plan, editor, adjustment and coach stay usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 768 ? 844 : 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await installPlan(page)
    await confirmTestCoach(page)
    await mkdir(output, { recursive: true })
    await expectSafeCoach(page)
    await expect(page.getByRole('navigation', { name: '主要导航' })).toBeVisible({ visible: width >= 1024 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
    await page.screenshot({ path: path.join(output, `plan-${width}.png`), fullPage: true })

    await page.locator('.action-summary').first().click()
    const editor = page.getByRole('dialog', { name: '调整动作' })
    await expect(editor).toBeVisible()
    await expect(page.locator('.floating-coach')).toBeHidden()
    await expect(editor.getByLabel('动作名称', { exact: true })).toBeFocused()
    const box = await editor.boundingBox()
    if (width >= 1024) {
      expect(box!.height).toBe(900)
      expect(box!.width).toBeLessThanOrEqual(500)
    } else expect(box!.width).toBe(width)
    await page.screenshot({ path: path.join(output, `editor-${width}.png`) })
    await page.keyboard.press('Escape')
    await expect(page.locator('.action-summary').first()).toBeFocused()

    await page.locator('[data-adjustment-trigger]').click()
    const dialog = page.getByRole('dialog', { name: '调整这次训练' })
    await expect(dialog.locator('.adjustment-change-list')).toHaveCount(0)
    await dialog.locator('[data-adjustment-intent="easier_to_finish"]').click()
    await page.screenshot({ path: path.join(output, `intent-${width}.png`) })
    await dialog.locator('[data-generate-adjustment]').click()
    await expect(dialog.locator('.adjustment-intents')).toHaveCount(0)
    await expect(dialog.locator('[data-preview-heading]')).toBeFocused()
    await expect(page.locator('.action-summary').first()).toContainText('每组 30 秒')
    await page.screenshot({ path: path.join(output, `preview-${width}.png`) })
    expect((await new AxeBuilder({ page }).include('.adjustment-dialog').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([])
    await dialog.locator('[data-edit-adjustment-intent]').click()
    await dialog.locator('[data-adjustment-intent="more_challenging"]').click()
    await dialog.locator('[data-generate-adjustment]').click()
    await dialog.locator('[data-apply-adjustment]').click()
    await expect(page.locator('[data-adjustment-trigger]')).toBeFocused()
    await expect(page.locator('.action-summary').first()).toContainText('每组 35 秒')
    await expectSafeCoach(page)
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([])
  })
}

test('coach supports mouse drag, keyboard, persistence, collapse and resize without changing the plan', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installPlan(page)
  await confirmTestCoach(page)
  const beforePlan = await page.locator('.plan-list').innerText()
  const handle = page.getByRole('button', { name: '移动小猫教练，使用方向键或拖动' })
  const box = (await handle.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  // The C layout has full-width action cards; drag into the free lower area.
  await page.mouse.move(850, 880, { steps: 12 })
  await page.mouse.up()
  await expect(page.getByRole('button', { name: '收起小猫教练' })).toBeEnabled()
  const moved = (await page.locator('.floating-coach').boundingBox())!
  expect(moved.x).toBeLessThan(box.x - 100)
  await page.reload()
  await expect(page.locator('.floating-coach')).toBeVisible()
  expect((await page.locator('.floating-coach').boundingBox())!.x).toBeCloseTo(moved.x, 0)
  await handle.focus()
  await handle.press('ArrowLeft')
  await expect(page.getByRole('button', { name: '收起小猫教练' })).toBeEnabled()
  await expect.poll(async () => (await page.locator('.floating-coach').boundingBox())!.x).toBeLessThan(moved.x - 20)
  await page.getByRole('button', { name: '收起小猫教练' }).click()
  await expect(page.getByRole('button', { name: '恢复小猫教练' })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '恢复小猫教练' }).click()
  await expect(handle).toBeVisible()
  await page.setViewportSize({ width: 320, height: 650 })
  await expectSafeCoach(page)
  const mobile = (await page.locator('.floating-coach').boundingBox())!
  expect([8, 200]).toContain(mobile.x)
  expect(await page.locator('.plan-list').innerText()).toBe(beforePlan)
})

test('coach leaves back, empty-plan and save-retry controls clickable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installPlan(page)
  await confirmTestCoach(page)

  const dragOnto = async (name: string, role: 'button' | 'link' = 'button') => {
    const target = page.getByRole(role, { name, exact: true })
    const targetBox = (await target.boundingBox())!
    const handle = page.getByRole('button', { name: '移动小猫教练，使用方向键或拖动' })
    const cat = (await handle.boundingBox())!
    await page.mouse.move(cat.x + cat.width / 2, cat.y + cat.height / 2)
    await page.mouse.down()
    await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 10 })
    await page.mouse.up()
    await expectSafeCoach(page)
    const bounds = (await page.locator('.floating-coach').boundingBox())!
    expect(bounds.x + bounds.width <= targetBox.x || bounds.x >= targetBox.x + targetBox.width
      || bounds.y + bounds.height <= targetBox.y || bounds.y >= targetBox.y + targetBox.height).toBe(true)
  }
  await dragOnto('返回训练', 'link')
  for (let i = 0; i < 3; i++) {
    await page.locator('.action-summary').first().click()
    await page.getByRole('button', { name: '删除动作', exact: true }).click()
  }
  await expect(page.getByRole('heading', { name: '还没有训练动作' })).toBeVisible()
  await dragOnto('使用快速体验方案')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(3)
  await page.evaluate(() => {
    const original = IDBObjectStore.prototype.put
    Object.defineProperty(window, '__restoreDraftWrites', { value: () => { IDBObjectStore.prototype.put = original } })
    IDBObjectStore.prototype.put = function (...args) {
      if (this.name === 'drafts') throw new DOMException('synthetic test failure', 'QuotaExceededError')
      return original.apply(this, args)
    }
  })
  await page.getByLabel('方案名称').fill('保存重试验收')
  await page.getByLabel('方案名称').press('Tab')
  await expect(page.getByRole('button', { name: '重试保存当前方案' })).toBeVisible()
  await dragOnto('重试保存当前方案')
  await page.evaluate(() => Reflect.get(window, '__restoreDraftWrites')())
  await page.getByRole('button', { name: '重试保存当前方案' }).click()
  await expect(page.locator('.save-state')).toContainText('已自动保存到本机')
  await page.getByRole('link', { name: '返回训练', exact: true }).click()
  await expect(page.locator('.saved-row').filter({ hasText: '保存重试验收' })).toHaveCount(1)
  await page.reload()
  await expect(page.locator('.saved-row').filter({ hasText: '保存重试验收' })).toHaveCount(1)
})
