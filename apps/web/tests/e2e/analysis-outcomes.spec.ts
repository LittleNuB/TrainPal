import { expect, type Page, test } from '@playwright/test'

type TerminalOutcome = 'empty' | 'failed'

const now = '2026-07-21T00:00:00.000Z'

const runView = (runId: string, outcome: TerminalOutcome) => ({
  id: runId,
  source_id: 'e2e-source-01',
  trigger_seconds: 2,
  status: outcome === 'empty' ? 'completed' : 'failed',
  stage: outcome === 'empty' ? 'completed' : 'failed',
  candidates: [],
  warnings: [],
  empty_reason: outcome === 'empty' ? 'no_evidence' : null,
  error: outcome === 'failed'
    ? { code: 'provider_error', message: '动作分析暂时不可用，请重试', retryable: true }
    : null,
  source_duration_seconds: 4,
  processed_seconds: 4,
  discovered_candidate_count: 0,
  coverage_status: 'complete',
  coverage_gaps: [],
  created_at: now,
  updated_at: now,
})

const mockTerminalRun = async (page: Page, outcome: TerminalOutcome): Promise<void> => {
  const runId = `run-${outcome}`
  await page.route('**/api/v1/analysis-runs', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 202,
      json: {
        ...runView(runId, outcome),
        status: 'queued',
        stage: 'queued',
        empty_reason: null,
        error: null,
      },
    })
  })
  await page.route(`**/api/v1/analysis-runs/${runId}`, async (route) => {
    await route.fulfill({ status: 200, json: runView(runId, outcome) })
  })
  await page.route(`**/api/v1/analysis-runs/${runId}/events`, async (route) => {
    const eventName = outcome === 'empty' ? 'run.completed' : 'run.failed'
    const data = {
      stage: outcome === 'empty' ? 'completed' : 'failed',
      source_duration_seconds: 4,
      processed_seconds: 4,
      discovered_candidate_count: 0,
      coverage_status: 'complete',
      coverage_gaps: [],
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: `event: ${eventName}\ndata: ${JSON.stringify({
        sequence: 1,
        type: eventName,
        run_id: runId,
        data,
      })}\n\n`,
    })
  })
}

const startAnalysis = async (page: Page): Promise<void> => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /刷到的动作/ })).toBeVisible()
  await page.getByRole('button', { name: '0:02', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认并开始' }).click()
  await expect(page).toHaveURL(/\/analysis$/)
}

test('AI empty result stays separate from failures and contains no fallback candidate', async ({ page }) => {
  await mockTerminalRun(page, 'empty')
  await startAnalysis(page)

  await expect(page.getByText('没有足够可靠的动作证据', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '手工创建动作' })).toBeVisible()
  await expect(page.locator('.completion-card')).toHaveCount(0)
  await expect(page.getByText('这次没有分析成功', { exact: true })).toHaveCount(0)
  await page.goto('/train')
  await expect(page.locator('.saved-row')).toHaveCount(0)
})

test('AI system failure offers retry and contains no fallback candidate', async ({ page }) => {
  await mockTerminalRun(page, 'failed')
  await startAnalysis(page)

  await expect(page.getByText('这次没有分析成功', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重试分析' })).toBeVisible()
  await expect(page.locator('.completion-card')).toHaveCount(0)
  await expect(page.getByText('没有足够可靠的动作证据', { exact: true })).toHaveCount(0)
  await page.goto('/train')
  await expect(page.locator('.saved-row')).toHaveCount(0)
})

test('AI capacity exhaustion shows Retry-After and the labelled quick path', async ({ page }) => {
  await page.route('**/api/v1/analysis-runs', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 429,
      headers: { 'Content-Type': 'application/json', 'Retry-After': '12' },
      json: { detail: '真实动作分析暂时繁忙，请稍后重试' },
    })
  })
  await startAnalysis(page)

  await expect(page.getByText('实时 AI 名额正在使用', { exact: true })).toBeVisible()
  await expect(page.getByText('约 12 秒后可重试', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '使用快速体验方案' })).toBeVisible()
  await expect(page.locator('.completion-card')).toHaveCount(0)
})
