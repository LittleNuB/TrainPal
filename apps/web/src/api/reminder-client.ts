import { apiFailure } from './failure'
import { appUrl } from './url'
import type { components, operations } from '@/api/schema'
import { fingerprintTrainingReminderInput } from '@/api/reminder-fingerprint'

export type TrainingReminderRequest = operations['plan_training_reminders_api_v1_training_reminders_post']['requestBody']['content']['application/json']
export type TrainingReminderPlan = components['schemas']['ReminderPlan']

const invalid = (): never => { throw new Error('提醒安排已失效，训练方案保持不变') }
const integer = (value: number, minimum: number, maximum = Number.MAX_SAFE_INTEGER): boolean =>
  Number.isSafeInteger(value) && value >= minimum && value <= maximum
const onlyKeys = (value: object, keys: string[]): boolean => Object.keys(value).every((key) => keys.includes(key))

export const validateTrainingReminderPlan = async (input: TrainingReminderRequest, value: unknown): Promise<TrainingReminderPlan> => {
  if (!value || typeof value !== 'object') return invalid()
  const plan = value as TrainingReminderPlan
  if (plan.plan_id !== input.plan_id || plan.plan_revision !== input.plan_revision
    || plan.contract_version !== 'training-reminders.v1.1' || plan.skill_version !== '1.0.0'
    || plan.policy_version !== 'ReminderPolicy.v1'
    || typeof plan.input_fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(plan.input_fingerprint)
    || !['model', 'rules', 'disabled'].includes(plan.mode)
    || !Array.isArray(plan.reminders) || !Array.isArray(plan.budgets)
    || !onlyKeys(plan, ['plan_id', 'plan_revision', 'input_fingerprint', 'contract_version',
      'skill_version', 'policy_version', 'mode', 'fallback_reason', 'reminders', 'budgets'])
    || plan.reminders.length > 1000 || plan.budgets.length > 200) return invalid()
  if ((plan.mode === 'model' || plan.mode === 'disabled') && plan.fallback_reason !== null) return invalid()
  if (plan.mode === 'rules' && !['model_unavailable', 'model_failure', 'model_timeout',
    'invalid_selection', 'skill_unavailable'].includes(plan.fallback_reason ?? '')) return invalid()
  if (plan.mode === 'disabled' && (plan.reminders.length || plan.budgets.length)) return invalid()

  const seen = new Set<string>()
  const slots = new Set<string>()
  for (const event of plan.reminders) {
    if (!event || typeof event !== 'object') return invalid()
    const action = input.actions.find((entry) => entry.action_id === event.action_id)
    const tip = action?.tips?.find((entry) => entry.tip_id === event.tip_id)
    const key = JSON.stringify([event.action_id, event.tip_id])
    if (!action?.confirmed || !input.preferences.confirmed || !input.preferences.automatic_speech
      || action.action_revision !== event.action_revision || !tip || tip.review_status !== 'approved'
      || tip.review_action_id !== action.action_id || tip.review_action_revision !== action.action_revision
      || !action.source_id || tip.source_id !== action.source_id
      || !integer(event.set_number, 1, action.sets) || !integer(event.priority, 1, 5)
      || event.max_plays !== 1 || seen.has(key)
      || !onlyKeys(event, ['action_id', 'action_revision', 'tip_id', 'stage', 'trigger',
        'set_number', 'active_seconds', 'priority', 'max_plays'])) return invalid()
    const range = action.source_segment
    const evidence = tip.tip.evidence
    if (!range || ![range.start_seconds, range.end_seconds, evidence.start_seconds,
      evidence.end_seconds].every(Number.isFinite) || range.start_seconds < 0 || range.end_seconds > 300
      || range.end_seconds <= range.start_seconds || evidence.start_seconds < range.start_seconds
      || evidence.end_seconds > range.end_seconds || evidence.end_seconds <= evidence.start_seconds) return invalid()
    seen.add(key)
    const slot = JSON.stringify([event.action_id, event.trigger, event.set_number, event.active_seconds])
    if (slots.has(slot)) return invalid()
    slots.add(slot)
    if (tip.tip.category === 'setup') {
      if (event.stage !== 'prepare' || event.trigger !== 'action_prepare'
        || event.set_number !== 1 || event.active_seconds !== null) return invalid()
    } else {
      if (event.stage !== 'training') return invalid()
      if (event.trigger === 'set_start') {
        if (event.active_seconds !== null || (action.mode === 'duration'
          && (action.duration_seconds ?? 0) <= 10)) return invalid()
      } else if (event.trigger === 'active_elapsed') {
        if (action.mode !== 'duration' || !integer(event.active_seconds ?? 0, 30)
          || (event.active_seconds ?? 0) % 30 !== 0
          || (event.active_seconds ?? 0) >= (action.duration_seconds ?? 0) - 10) return invalid()
      } else return invalid()
    }
  }
  const counts = new Map<string, number>()
  for (const event of plan.reminders) counts.set(event.action_id, (counts.get(event.action_id) ?? 0) + 1)
  const budgetIds = new Set<string>()
  for (const budget of plan.budgets) {
    if (!budget || typeof budget !== 'object' || budgetIds.has(budget.action_id)
      || !integer(budget.utterance_limit, 1, 5) || counts.get(budget.action_id) !== budget.utterance_limit
      || budget.active_interval_seconds !== 30 || budget.end_quiet_seconds !== 10
      || !onlyKeys(budget, ['action_id', 'utterance_limit', 'active_interval_seconds', 'end_quiet_seconds'])) return invalid()
    budgetIds.add(budget.action_id)
  }
  if (budgetIds.size !== counts.size) return invalid()
  if (plan.input_fingerprint !== await fingerprintTrainingReminderInput(input)) return invalid()
  return plan
}

export const requestTrainingReminders = async (
  input: TrainingReminderRequest, signal: AbortSignal,
): Promise<TrainingReminderPlan> => {
  signal.throwIfAborted()
  const body = JSON.stringify(input)
  const frozen = JSON.parse(body) as TrainingReminderRequest
  if (new TextEncoder().encode(body).byteLength > 64000) return invalid()
  const response = await fetch(appUrl('/api/v1/training-reminders'), {
    method: 'POST', credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json' }, body,
  })
  if (!response.ok) throw await apiFailure(response, new Error('提醒安排暂不可用，仍可开始训练和查看动作要点'))
  const result: unknown = await response.json()
  signal.throwIfAborted()
  const plan = await validateTrainingReminderPlan(frozen, result)
  signal.throwIfAborted()
  return plan
}
