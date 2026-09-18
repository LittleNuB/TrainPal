import { expect, test } from '@playwright/test'

test('source tips and conflicting values survive editing, archiving and reopening', async ({ page }) => {
  const result = {
    id: 'sources-fixture', source_id: 'e2e-source-01', trigger_seconds: null,
    status: 'completed', stage: 'completed', warnings: [],
    empty_reason: null, error: null, source_duration_seconds: 2, processed_seconds: 2,
    discovered_candidate_count: 1, coverage_status: 'complete', coverage_gaps: [],
    created_at: '2026-09-19T00:00:00Z', updated_at: '2026-09-19T00:00:01Z',
    candidates: [{
      id: 'candidate-1', source_id: 'e2e-source-01', name: '标准俯卧撑',
      segment: { start_seconds: 0.2, end_seconds: 1.8 },
      parameters: { mode: 'reps', sets: null, reps: 12, reps_max: 15, duration_seconds: null, rest_seconds: 45 },
      evidence: [{ type: 'speech', start_seconds: 0.3, end_seconds: 1.7 },
        { type: 'visual', start_seconds: 0.2, end_seconds: 1.8 }],
      tips: [{ text: '推起时呼气', category: 'breathing',
        evidence: { type: 'speech', start_seconds: 0.3, end_seconds: 1.7 } }],
      parameter_conflicts: [{ field: 'sets', alternatives: [
        { parameters: { sets: 3 }, evidence: [{ type: 'speech', start_seconds: 0.3, end_seconds: 1.7 }] },
        { parameters: { sets: 4 }, evidence: [{ type: 'visual', start_seconds: 0.2, end_seconds: 1.8 }] },
      ] }],
      needs_confirmation: true,
    }],
  }
  await page.route('**/api/v1/analysis-runs', route => route.fulfill({ status: 202,
    json: { ...result, status: 'queued', stage: 'queued', candidates: [] } }))
  await page.route('**/api/v1/analysis-runs/sources-fixture', route => route.fulfill({ json: result }))
  await page.route('**/api/v1/analysis-runs/sources-fixture/events', route => route.fulfill({
    contentType: 'text/event-stream', body: `event: run.completed\ndata: ${JSON.stringify({
      sequence: 1, type: 'run.completed', run_id: result.id,
      data: { stage: 'completed', source_duration_seconds: 2, processed_seconds: 2,
        discovered_candidate_count: 1, coverage_status: 'complete', coverage_gaps: [] },
    })}\n\n`,
  }))
  await page.goto('/')
  await page.locator('.quick-real-section summary').click()
  await page.getByRole('button', { name: '0:02', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
  await page.getByRole('button', { name: '查看训练方案' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(1)
  await page.locator('.action-summary').click()
  const sheet = page.locator('.action-sheet')
  await expect(sheet.getByLabel('动作名称', { exact: true })).toHaveValue('标准俯卧撑')
  await expect(sheet.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
  await expect(sheet.getByText('视频中有不同说法，请核对后设置')).toBeVisible()
  await expect(sheet.locator('.source-parameter-note li').nth(0)).toContainText('3 组')
  await expect(sheet.locator('.source-parameter-note li').nth(1)).toContainText('4 组')
  await expect(sheet.getByText('视频建议：每组 12～15 次')).toBeVisible()
  await sheet.getByRole('spinbutton').nth(1).fill('10')
  await sheet.getByRole('button', { name: '确认并加入' }).click()
  await page.getByRole('link', { name: '返回训练', exact: true }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '训练', exact: true }).click()
  await expect(page.locator('.saved-row')).toHaveCount(1)
  page.once('dialog', dialog => dialog.accept())
  await page.locator('.saved-row').getByRole('button', { name: '打开' }).click()
  await expect(page).toHaveURL(/\/plan$/)
  await page.reload()
  await page.locator('.action-summary').click()
  await expect(sheet.getByRole('spinbutton').nth(1)).toHaveValue('10')
  await expect(sheet.getByText('你的调整', { exact: true }).first()).toBeVisible()
  await expect(sheet.getByText('推起时呼气', { exact: true }).first()).toBeVisible()
  await expect(sheet.locator('.source-parameter-note li')).toHaveCount(2)
})
