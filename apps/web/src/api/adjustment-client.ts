import type { AdjustmentChange, AdjustmentProposal } from '@/domain/personalization'
import { UserMessageError } from '@/domain/user-message'
import {
  adjustmentEnvelopes, estimateSeconds, latestFeedback, proposePlanAdjustment,
  type AdjustmentPolicyInput,
} from '@/personalization/adjustment-policy'

const failure = (): Error => new UserMessageError('这次未能生成合适的调整，请重试或直接编辑数字。')
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export async function requestPlanAdjustment(
  input: AdjustmentPolicyInput, signal: AbortSignal,
): Promise<AdjustmentProposal> {
  const envelope = proposePlanAdjustment(input)
  const allowed = adjustmentEnvelopes(input)
  if (!allowed.length) return { ...envelope, id: `adjustment-policy.v3:${envelope.inputFingerprint}`,
    policyVersion: 'adjustment-policy.v3', origin: 'protected', status: 'no_change', changes: [],
    summary: '当前方案保持不变，可以直接编辑动作数字。',
    impact: { estimatedSecondsBefore: estimateSeconds(input.basePlan.items),
      estimatedSecondsAfter: estimateSeconds(input.basePlan.items), deltaRatio: 0 } }
  const response = await fetch('/api/v1/plan-adjustments', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    signal: AbortSignal.any([signal, AbortSignal.timeout(30_000)]),
    body: JSON.stringify({
      input_fingerprint: envelope.inputFingerprint, plan_revision: envelope.basePlanRevision,
      intent: input.intent, experience: input.trainingExperience,
      available_minutes: input.availableMinutes ?? null, energy: input.energy ?? 'usual',
      safety_stop: input.hasSafetyStopSignal,
      actions: input.basePlan.items.filter(item => item.confirmationStatus !== 'pending').map(item => ({
        id: item.id, name: item.name, mode: item.mode,
        confirmed: item.confirmationStatus !== 'pending', sets: item.sets, reps: item.reps,
        durationSeconds: item.durationSeconds, restSeconds: item.restSeconds,
        feedback: latestFeedback(input.signals, item.id) ?? null,
        reps_range: item.sourceParameters?.reps != null && item.sourceParameters.reps_max != null
          ? [item.sourceParameters.reps, item.sourceParameters.reps_max] : null,
      })),
    }),
  })
  if (!response.ok) throw failure()
  const result: unknown = await response.json()
  if (!record(result) || !Array.isArray(result.changes)
    || result.input_fingerprint !== envelope.inputFingerprint
    || result.plan_revision !== envelope.basePlanRevision
    || !['model', 'protected'].includes(String(result.mode))
    || typeof result.summary !== 'string' || !result.summary.trim() || result.summary.length > 240) throw failure()
  const seen = new Set<string>()
  const loads = new Set<string>()
  const changes: AdjustmentChange[] = result.changes.map((change: unknown) => {
    if (!record(change) || typeof change.value !== 'number' || !Number.isInteger(change.value)
      || typeof change.reason !== 'string' || !change.reason.trim() || change.reason.length > 180) throw failure()
    const candidate = allowed.find(item => item.itemId === change.action_id && item.field === change.field)
    if (!candidate) throw failure()
    const key = `${candidate.itemId}:${candidate.field}`
    const delta = change.value - candidate.before.value
    const bounds = candidate.policyBounds
    const step = candidate.field === 'durationSeconds' || candidate.field === 'restSeconds' ? 5 : 1
    if (seen.has(key) || !delta || delta % step !== 0
      || Math.sign(delta) !== Math.sign(candidate.after.value - candidate.before.value)
      || Math.abs(delta) > bounds.maximumDelta || change.value < bounds.minimum || change.value > bounds.maximum
      || (candidate.field !== 'restSeconds' && loads.has(candidate.itemId))) throw failure()
    seen.add(key)
    if (candidate.field !== 'restSeconds') loads.add(candidate.itemId)
    return { ...candidate, after: { value: change.value, source: 'personalized' }, explanation: change.reason }
  })
  if (result.mode === 'protected' && (changes.length || allowed.length)) throw failure()
  if (input.intent === 'more_challenging' && input.energy !== 'tired' && input.trainingExperience === 'beginner'
    && loads.size > Math.floor(input.basePlan.items.filter(item => item.confirmationStatus !== 'pending').length / 2)) throw failure()
  const items = structuredClone(input.basePlan.items)
  for (const change of changes) items.find(item => item.id === change.itemId)![change.field] = change.after
  const before = estimateSeconds(input.basePlan.items)
  const after = estimateSeconds(items)
  if (Math.abs(after - before) > before * 0.2 + 1e-6) throw failure()
  return {
    ...envelope, id: `adjustment-policy.v3:${envelope.inputFingerprint}:${crypto.randomUUID()}`,
    policyVersion: 'adjustment-policy.v3', origin: result.mode as 'model' | 'protected',
    summary: result.summary, changes, status: changes.length ? 'ready' : 'no_change',
    impact: { estimatedSecondsBefore: before, estimatedSecondsAfter: after, deltaRatio: before ? (after - before) / before : 0 },
  }
}
