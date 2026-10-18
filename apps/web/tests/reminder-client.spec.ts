import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { requestTrainingReminders, type TrainingReminderRequest } from '@/api/reminder-client'
import fingerprintVectors from '../../../contracts/fixtures/training-reminders-v1.1.json'

const input = () => ({
  plan_id: 'synthetic-plan', plan_revision: 3,
  preferences: { automatic_speech: true, confirmed: true },
  actions: [{
    action_id: 'synthetic-action', action_revision: 1, name: '深蹲', confirmed: true,
    source_id: 'synthetic-source', source_segment: { start_seconds: 0, end_seconds: 20 },
    mode: 'duration' as const, sets: 1, duration_seconds: 15,
    tips: [{
      tip_id: 'synthetic-tip', review_status: 'approved' as const,
      review_action_id: 'synthetic-action', review_action_revision: 1, source_id: 'synthetic-source',
      tip: { text: '起身时呼气', category: 'breathing' as const,
        evidence: { type: 'speech' as const, start_seconds: 5, end_seconds: 8 } },
    }],
  }],
})

const output = () => ({
  plan_id: 'synthetic-plan', plan_revision: 3,
  input_fingerprint: 'fd88538092001b5382c569c976543c0709ce7ca2c2919b1bbfe271816f737420',
  contract_version: 'training-reminders.v1.1', skill_version: '1.0.0', policy_version: 'ReminderPolicy.v1',
  mode: 'model', fallback_reason: null,
  reminders: [{ action_id: 'synthetic-action', action_revision: 1, tip_id: 'synthetic-tip',
    stage: 'training', trigger: 'set_start', set_number: 1, active_seconds: null, priority: 1, max_plays: 1 }],
  budgets: [{ action_id: 'synthetic-action', utterance_limit: 1, active_interval_seconds: 30, end_quiet_seconds: 10 }],
})

beforeEach(() => vi.stubGlobal('crypto', webcrypto))
afterEach(() => vi.unstubAllGlobals())

it('requests a same-origin bound reminder arrangement without changing its input', async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(output())))
  vi.stubGlobal('fetch', fetcher)
  const payload = input()
  const before = JSON.stringify(payload)
  const signal = new AbortController().signal
  const result = await requestTrainingReminders(payload, signal)
  expect(result).toEqual(output())
  expect(fetcher).toHaveBeenCalledWith('/api/v1/training-reminders', expect.objectContaining({
    method: 'POST', credentials: 'same-origin', signal, body: before,
  }))
  expect(JSON.stringify(payload)).toBe(before)
})

it.each(['plan', 'revision', 'tip', 'contract', 'training-time'])(
  'rejects a response with an invalid %s binding before it can be used', async (failure) => {
    const result = output()
    if (failure === 'plan') result.plan_id = 'another-plan'
    if (failure === 'revision') result.plan_revision = 2
    if (failure === 'tip') result.reminders[0]!.tip_id = 'invented-tip'
    if (failure === 'contract') result.contract_version = 'unknown-contract'
    if (failure === 'training-time') {
      result.reminders[0]!.trigger = 'active_elapsed'
      Object.assign(result.reminders[0]!, { active_seconds: 14 })
    }
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(result))))
    await expect(requestTrainingReminders(input(), new AbortController().signal)).rejects.toThrow('提醒')
  },
)

it('discards late responses after cancellation even if the transport ignores abort', async () => {
  const controller = new AbortController()
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async () => {
    controller.abort()
    return new Response(JSON.stringify(output()))
  }))
  await expect(requestTrainingReminders(input(), controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
})

it.each(['raw-content', 'event-content', 'budget', 'source-range', 'disabled'])(
  'rejects unsafe %s rather than returning it for persistence', async (failure) => {
    const payload = input()
    const result = output()
    if (failure === 'raw-content') Object.assign(result, { model_response: 'not allowed' })
    if (failure === 'event-content') Object.assign(result.reminders[0]!, { text: 'invented content' })
    if (failure === 'budget') result.budgets[0]!.utterance_limit = 5
    if (failure === 'source-range') payload.actions[0]!.tips[0]!.tip.evidence.end_seconds = 25
    if (failure === 'disabled') result.mode = 'disabled'
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(result))))
    await expect(requestTrainingReminders(payload, new AbortController().signal)).rejects.toThrow('提醒')
  },
)

it('rejects an oversized UTF-8 request before sending it', async () => {
  const payload = input()
  payload.actions[0]!.name = '合成'.repeat(16000)
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(output())))
  vi.stubGlobal('fetch', fetcher)
  await expect(requestTrainingReminders(payload, new AbortController().signal)).rejects.toThrow('提醒')
  expect(fetcher).not.toHaveBeenCalled()
})

it('rejects a well-shaped fingerprint belonging to different complete input', async () => {
  const result = output()
  result.input_fingerprint = 'b'.repeat(64)
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(result))))
  await expect(requestTrainingReminders(input(), new AbortController().signal)).rejects.toThrow('提醒')
})

it.each(fingerprintVectors)('accepts the shared known fingerprint for $case', async (vector) => {
  const result = { ...output(), input_fingerprint: vector.expected_fingerprint }
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(result))))
  expect(await requestTrainingReminders(vector.request as TrainingReminderRequest, new AbortController().signal)).toEqual(result)
})

it('rejects a genuine fingerprint for an earlier version of the same action content', async () => {
  const payload = input()
  payload.actions[0]!.name = '合成深蹲教学'
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(output()))))
  await expect(requestTrainingReminders(payload, new AbortController().signal)).rejects.toThrow('提醒')
})
