import type { operations } from '@/api/schema'

type Input = operations['plan_training_reminders_api_v1_training_reminders_post']['requestBody']['content']['application/json']

const encodeNumbers = (value: unknown): unknown => {
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      throw new Error('提醒输入包含无法准确表示的数值')
    }
    if (Number.isInteger(value)) return { $integer: String(value) }
    const bytes = new Uint8Array(8)
    new DataView(bytes.buffer).setFloat64(0, value, false)
    return { $float64: Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('') }
  }
  if (Array.isArray(value)) return value.map(encodeNumbers)
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, encodeNumbers((value as Record<string, unknown>)[key])]),
  )
  return value
}

export const fingerprintTrainingReminderInput = async (input: Input): Promise<string> => {
  // Match the validated Pydantic defaults, not whichever optional fields the
  // caller happened to include. Numeric tags make 1/1.0 and exponent spellings equal.
  const normalized = {
    plan_id: input.plan_id, plan_revision: input.plan_revision,
    preferences: input.preferences,
    actions: input.actions.map((action) => ({
      ...action, source_id: action.source_id ?? null, source_segment: action.source_segment ?? null,
      reps: action.reps ?? null, duration_seconds: action.duration_seconds ?? null,
      tips: (action.tips ?? []).map((tip) => ({ ...tip, review_status: tip.review_status ?? 'unknown' })),
    })),
  }
  const value = JSON.stringify(encodeNumbers({
    contract: 'training-reminders.v1.1', skill: '1.0.0', policy: 'ReminderPolicy.v1', input: normalized,
  }))
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
