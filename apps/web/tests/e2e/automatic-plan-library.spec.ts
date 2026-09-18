import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test('pending library writes prevent leaving for an editable draft', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await page.evaluate(() => {
    Reflect.set(window, '__holdDraftWrite', true)
    const put = IDBObjectStore.prototype.put
    IDBObjectStore.prototype.put = function (...args) {
      const result = put.apply(this, args)
      if (this.name === 'drafts') {
        Reflect.set(window, '__draftWriteStarted', true)
        const keepOpen = () => {
          if (Reflect.get(window, '__holdDraftWrite')) this.count().onsuccess = keepOpen
        }
        keepOpen()
      }
      return result
    }
  })
  try {
    await page.getByRole('button', { name: '新建方案', exact: true }).click()
    await expect.poll(() => page.evaluate(() => Reflect.get(window, '__draftWriteStarted'))).toBe(true)
    await page.getByRole('link', { name: '检查并开始', exact: true }).click()
    await expect(page).toHaveURL(new RegExp('/train$'))
  } finally {
    await page.evaluate(() => Reflect.set(window, '__holdDraftWrite', false))
  }
  await expect(page).toHaveURL(new RegExp('/plan$'))
  await expect(page.getByRole('button', { name: /创建动作/ })).toBeVisible()
})

for (const width of [320, 1440]) {
  test(`populated plan library remains usable at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/train')
    await page.getByRole('button', { name: '使用快速体验方案' }).click()
    await page.getByRole('link', { name: '返回首页', exact: true }).click()
    await page.locator('a[href="/train"]').click()
    await expect(page.locator('.saved-row')).toHaveCount(1)
    await expect(page.getByRole('button', { name: '新建方案', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    const accessibility = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    expect(accessibility.violations).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('plan-library.png'), fullPage: true })
  })
}

test('a recognized legacy sample keeps its label after renaming and copying', async ({ page }) => {
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await expect(page).toHaveURL(/\/plan$/)
  await page.evaluate(() => new Promise<void>((resolve, reject) => {
    const request = indexedDB.open('hachimi-fitness')
    request.onerror = () => reject(request.error)
    request.onsuccess = () => {
      const db = request.result
      const transaction = db.transaction(['drafts', 'plans'], 'readwrite')
      for (const table of ['drafts', 'plans']) {
        const store = transaction.objectStore(table)
        const records = store.getAll()
        records.onsuccess = () => {
          for (const record of records.result) {
            for (const item of record.items) delete item.origin
            store.put(record)
          }
        }
      }
      transaction.oncomplete = () => { db.close(); resolve() }
      transaction.onabort = () => { db.close(); reject(transaction.error) }
    }
  }))
  await page.reload()
  await page.getByLabel('方案名称', { exact: true }).fill('旧样例改名')
  await page.getByLabel('方案名称', { exact: true }).press('Tab')
  await expect(page.locator('.quick-notice')).toBeVisible()
  await page.getByRole('button', { name: '复制方案', exact: true }).click()
  await page.getByRole('button', { name: '创建副本', exact: true }).click()
  await expect(page.getByText('已创建方案副本', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('方案名称', { exact: true })).toHaveValue('旧样例改名（副本）')
  await expect(page.locator('.quick-notice')).toBeVisible()
})

test('editing a deleted plan in an older tab reports failure without resurrecting it', async ({ page, context }) => {
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  const olderTab = await context.newPage()
  await olderTab.goto('/plan')
  await expect(olderTab.getByLabel('方案名称', { exact: true })).toHaveValue('8 分钟手臂唤醒')
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await page.getByRole('button', { name: '删除方案 8 分钟手臂唤醒', exact: true }).click()
  await expect(page.locator('.saved-row')).toHaveCount(0)
  await olderTab.getByLabel('方案名称', { exact: true }).fill('不应复活')
  await olderTab.getByLabel('方案名称', { exact: true }).press('Tab')
  await expect(olderTab.getByText('未保存，点击重试', { exact: true })).toBeVisible()
  await expect(olderTab.getByLabel('方案名称', { exact: true })).toHaveValue('不应复活')
  await page.reload()
  await expect(page.locator('.saved-row')).toHaveCount(0)
  await olderTab.close()
})

test('switching from an emptied editor saves the removed actions before loading a quick plan', async ({ page }) => {
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByLabel('方案名称', { exact: true }).fill('已清空的方案')
  await page.getByLabel('方案名称', { exact: true }).press('Tab')
  for (let index = 0; index < 3; index++) {
    await page.locator('.action-summary').first().click()
    await page.getByRole('button', { name: '删除动作', exact: true }).click()
  }
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await expect(page.locator('.saved-row')).toHaveCount(2)
  await expect(page.locator('.saved-row').filter({ hasText: '已清空的方案' })).toContainText('0 个动作')
})

test('an archived adjustment can still be restored after switching plans', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.locator('[data-adjustment-trigger]').click()
  await page.locator('[data-adjustment-intent="more_challenging"]').click()
  await page.locator('[data-generate-adjustment]').click()
  await page.locator('[data-apply-adjustment]').click()
  await expect(page.locator('[data-restore-base-plan]')).toBeVisible()
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await page.getByRole('button', { name: '新建方案', exact: true }).click()
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await page.locator('.plan-open').click()
  await expect(page.locator('.action-summary').first()).toContainText('35 秒')
  await page.locator('[data-restore-base-plan]').click()
  await expect(page.locator('.action-summary').first()).toContainText('30 秒')
})

test('starting a new manual plan keeps the previous plan and archives the new one', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await page.getByRole('button', { name: '新建方案', exact: true }).click()
  await page.getByRole('button', { name: /创建动作/ }).click()
  await page.getByLabel('动作名称', { exact: true }).fill('深蹲')
  await page.getByRole('button', { name: '加入方案', exact: true }).click()
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await expect(page.locator('.saved-row')).toHaveCount(2)
  await expect(page.locator('.saved-row').filter({ hasText: '深蹲训练方案' })).toHaveCount(1)
  await page.locator('.plan-open').filter({ hasText: '8 分钟手臂唤醒' }).click()
  await expect(page.getByLabel('方案名称', { exact: true })).toHaveValue('8 分钟手臂唤醒')
})

test('copying temporarily locks editing until the archive transaction completes', async ({ page }) => {
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByRole('button', { name: '复制方案', exact: true }).click()
  await page.getByLabel('新方案名称').fill('慢速复制')
  await page.evaluate(() => {
    Reflect.set(window, '__holdArchive', true)
    const add = IDBObjectStore.prototype.add
    IDBObjectStore.prototype.add = function (...args) {
      const result = add.apply(this, args)
      if (this.name === 'plans') {
        const keepTransactionOpen = () => {
          if (Reflect.get(window, '__holdArchive')) this.count().onsuccess = keepTransactionOpen
        }
        keepTransactionOpen()
      }
      return result
    }
  })
  await page.getByRole('button', { name: '创建副本', exact: true }).click()
  await expect(page.locator('.plan-workspace')).toHaveAttribute('inert', '')
  await page.evaluate(() => Reflect.set(window, '__holdArchive', false))
  await expect(page.getByText('已创建方案副本', { exact: true })).toBeVisible()
  await expect(page.locator('.plan-workspace')).not.toHaveAttribute('inert')
  await expect(page.getByLabel('方案名称', { exact: true })).toHaveValue('慢速复制')
})

test('an edited plan is archived without a separate save action and edits update it in place', async ({ page }) => {
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByLabel('方案名称', { exact: true }).fill('我的手臂方案')
  await page.getByLabel('方案名称', { exact: true }).press('Tab')
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await expect(page.locator('.saved-row')).toHaveCount(1)
  await expect(page.locator('.saved-row')).toContainText('我的手臂方案')
  await page.reload()
  await expect(page.locator('.saved-row')).toHaveCount(1)
  page.on('dialog', dialog => dialog.accept())
  await page.locator('.plan-open').click()
  await expect(page.getByLabel('方案名称', { exact: true })).toHaveValue('我的手臂方案')
})

test('copy creates a separate archive and deleting the active copy does not recreate it', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByRole('button', { name: '复制方案', exact: true }).click()
  await page.getByRole('textbox', { name: '新方案名称' }).fill('周末方案副本')
  await page.getByRole('button', { name: '创建副本', exact: true }).click()
  await expect(page.getByText('已创建方案副本', { exact: true })).toBeVisible()
  await expect(page.locator('.quick-notice')).toBeVisible()
  await page.getByRole('link', { name: '返回首页', exact: true }).click()
  await page.locator('a[href="/train"]').click()
  await expect(page.locator('.saved-row')).toHaveCount(2)
  await page.getByRole('button', { name: '删除方案 周末方案副本', exact: true }).click()
  await expect(page.locator('.saved-row')).toHaveCount(1)
  await page.reload()
  await expect(page.locator('.saved-row')).toHaveCount(1)
  await expect(page.locator('.saved-row')).not.toContainText('周末方案副本')
})
