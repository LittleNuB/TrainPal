import type { DraftItem, DraftPlan, ValueSource } from '@/domain/types'
import type {
  AdjustableField,
  AdjustmentChange,
  AdjustmentIntent,
  AdjustmentProposal,
  AdjustmentSignal,
  TrainingExperience,
} from '@/domain/personalization'

export type {
  AdjustableField,
  AdjustmentChange,
  AdjustmentIntent,
  AdjustmentProposal,
  AdjustmentReasonCode,
  AdjustmentSignal,
  TrainingExperience,
} from '@/domain/personalization'

export const ADJUSTMENT_POLICY_VERSION = 'adjustment-policy.v1' as const

export interface AdjustmentPolicyInput {
  basePlan: DraftPlan
  contextRevision: number
  intent: AdjustmentIntent
  trainingExperience: TrainingExperience | null
  signals: AdjustmentSignal[]
  hasSafetyStopSignal: boolean
  generatedAt: string
}

const adjustableSources = new Set<ValueSource>(['rule', 'personalized'])

const estimateSeconds = (items: DraftItem[]): number => items.reduce((total, item) => {
  if (item.confirmationStatus === 'pending') return total
  const sets = item.sets.value ?? 0
  const activePerSet = item.mode === 'duration'
    ? (item.durationSeconds.value ?? 0)
    : (item.reps.value ?? 0) * 3
  return total + sets * activePerSet + Math.max(0, sets - 1) * (item.restSeconds.value ?? 0)
}, 0)

const estimateItemSeconds = (item: DraftItem): number => estimateSeconds([item])

const hashValue = (value: unknown, prefix: string): string => {
  const serialized = JSON.stringify(value)
  let hash = 2_166_136_261
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index)
    hash = Math.imul(hash, 16_777_619)
  }
  return `${prefix}-${(hash >>> 0).toString(16).padStart(8, '0')}`
}

const planFingerprint = (plan: DraftPlan): string => hashValue({
    name: plan.name,
    linkedPlanId: plan.linkedPlanId,
    items: plan.items.map((item) => ({
      id: item.id,
      name: item.name,
      confirmationStatus: item.confirmationStatus ?? 'confirmed',
      mode: item.mode,
      sets: item.sets,
      reps: item.reps,
      durationSeconds: item.durationSeconds,
      restSeconds: item.restSeconds,
      weightKg: item.weightKg,
    })),
  }, 'plan')

const eligible = (value: number | null, source: ValueSource | null): value is number => (
  value !== null && source !== null && adjustableSources.has(source)
)

const applyChange = (items: DraftItem[], change: AdjustmentChange): void => {
  const item = items.find((candidate) => candidate.id === change.itemId)
  if (!item) return
  item[change.field] = { value: change.after.value, source: 'personalized' }
}

const cloneItems = (items: DraftItem[]): DraftItem[] => JSON.parse(JSON.stringify(items)) as DraftItem[]

const latestFeedback = (
  signals: AdjustmentSignal[],
  itemId: string,
): AdjustmentSignal['value'] | undefined => [...signals]
  .filter((signal) => signal.itemId === itemId)
  .sort((left, right) => right.recordedAt.localeCompare(left.recordedAt))[0]?.value

const challengingChange = (
  item: DraftItem,
  feedback: AdjustmentSignal['value'] | undefined,
): AdjustmentChange | null => {
  if (item.confirmationStatus === 'pending') return null
  if (feedback === 'too_hard' || feedback === 'just_right') return null
  const target = item.mode === 'duration' ? item.durationSeconds : item.reps
  const field: AdjustableField = item.mode === 'duration' ? 'durationSeconds' : 'reps'
  if (!eligible(target.value, target.source)) return null
  const maximum = item.mode === 'duration' ? 90 : 20
  const minimum = item.mode === 'duration' ? 10 : 5
  if (target.value < minimum || target.value > maximum) return null
  const maximumDelta = item.mode === 'duration'
    ? Math.min(10, Math.floor(target.value * 0.2 / 5) * 5)
    : Math.min(2, Math.floor(target.value * 0.2))
  const nextValue = Math.min(maximum, target.value + maximumDelta)
  if (nextValue === target.value) return null
  return {
    itemId: item.id,
    itemName: item.name,
    field,
    before: { value: target.value, source: target.source as 'rule' | 'personalized' },
    after: { value: nextValue, source: 'personalized' },
    reasonCode: feedback === 'too_easy' ? 'increase_after_too_easy' : 'increase_for_challenge',
    policyBounds: { minimum, maximum, maximumDelta },
  }
}

const easierChanges = (
  item: DraftItem,
  input: Pick<AdjustmentPolicyInput, 'trainingExperience' | 'signals'>,
): AdjustmentChange[] => {
  if (item.confirmationStatus === 'pending') return []
  const feedback = latestFeedback(input.signals, item.id)
  if (feedback === 'just_right' || feedback === 'too_easy') return []

  const changes: AdjustmentChange[] = []
  const target = item.mode === 'duration' ? item.durationSeconds : item.reps
  const field: AdjustableField = item.mode === 'duration' ? 'durationSeconds' : 'reps'
  if (eligible(target.value, target.source)) {
    const minimum = item.mode === 'duration' ? 10 : 5
    const maximum = item.mode === 'duration' ? 90 : 20
    if (target.value < minimum || target.value > maximum) return []
    const maximumDelta = item.mode === 'duration'
      ? Math.min(10, Math.floor(target.value * 0.2 / 5) * 5)
      : Math.min(2, Math.floor(target.value * 0.2))
    const nextValue = Math.max(minimum, target.value - maximumDelta)
    if (nextValue !== target.value) {
      changes.push({
        itemId: item.id,
        itemName: item.name,
        field,
        before: { value: target.value, source: target.source as 'rule' | 'personalized' },
        after: { value: nextValue, source: 'personalized' },
        reasonCode: 'reduce_to_finish',
        policyBounds: { minimum, maximum, maximumDelta },
      })
    }
  } else if (
    eligible(item.sets.value, item.sets.source)
    && item.sets.value > 1
    && item.sets.value <= 4
  ) {
    changes.push({
      itemId: item.id,
      itemName: item.name,
      field: 'sets',
      before: { value: item.sets.value, source: item.sets.source as 'rule' | 'personalized' },
      after: { value: item.sets.value - 1, source: 'personalized' },
      reasonCode: 'reduce_to_finish',
      policyBounds: { minimum: 1, maximum: 4, maximumDelta: 1 },
    })
  }

  if (
    (input.trainingExperience === 'beginner' || feedback === 'too_hard')
    && eligible(item.restSeconds.value, item.restSeconds.source)
    && item.restSeconds.value >= 30
    && item.restSeconds.value <= 180
  ) {
    const nextRest = Math.min(180, item.restSeconds.value + 15)
    if (nextRest !== item.restSeconds.value) {
      changes.push({
        itemId: item.id,
        itemName: item.name,
        field: 'restSeconds',
        before: {
          value: item.restSeconds.value,
          source: item.restSeconds.source as 'rule' | 'personalized',
        },
        after: { value: nextRest, source: 'personalized' },
        reasonCode: 'extend_recovery',
        policyBounds: { minimum: 30, maximum: 180, maximumDelta: 30 },
      })
    }
  }
  return changes
}

const shorteningCandidates = (item: DraftItem): AdjustmentChange[] => {
  if (item.confirmationStatus === 'pending') return []
  const candidates: AdjustmentChange[] = []
  if (
    eligible(item.sets.value, item.sets.source)
    && item.sets.value > 1
    && item.sets.value <= 4
  ) {
    candidates.push({
      itemId: item.id,
      itemName: item.name,
      field: 'sets',
      before: { value: item.sets.value, source: item.sets.source as 'rule' | 'personalized' },
      after: { value: item.sets.value - 1, source: 'personalized' },
      reasonCode: 'shorten_time_budget',
      policyBounds: { minimum: 1, maximum: 4, maximumDelta: 1 },
    })
  }
  const target = item.mode === 'duration' ? item.durationSeconds : item.reps
  const field: AdjustableField = item.mode === 'duration' ? 'durationSeconds' : 'reps'
  if (eligible(target.value, target.source)) {
    const minimum = item.mode === 'duration' ? 10 : 5
    const maximum = item.mode === 'duration' ? 90 : 20
    if (target.value < minimum || target.value > maximum) return candidates
    const maximumDelta = item.mode === 'duration'
      ? Math.min(10, Math.floor(target.value * 0.2 / 5) * 5)
      : Math.min(2, Math.floor(target.value * 0.2))
    const nextValue = Math.max(minimum, target.value - maximumDelta)
    if (nextValue !== target.value) {
      candidates.push({
        itemId: item.id,
        itemName: item.name,
        field,
        before: { value: target.value, source: target.source as 'rule' | 'personalized' },
        after: { value: nextValue, source: 'personalized' },
        reasonCode: 'shorten_time_budget',
        policyBounds: { minimum, maximum, maximumDelta },
      })
    }
  }
  return candidates
}

export function proposePlanAdjustment(input: AdjustmentPolicyInput): AdjustmentProposal {
  const fingerprint = planFingerprint(input.basePlan)
  const inputFingerprint = hashValue({
    basePlanFingerprint: fingerprint,
    contextRevision: input.contextRevision,
    intent: input.intent,
    trainingExperience: input.trainingExperience,
    signals: [...input.signals].sort((left, right) => (
      left.itemId.localeCompare(right.itemId)
      || left.recordedAt.localeCompare(right.recordedAt)
      || left.value.localeCompare(right.value)
    )),
    hasSafetyStopSignal: input.hasSafetyStopSignal,
  }, 'adjustment-input')
  const estimatedSecondsBefore = estimateSeconds(input.basePlan.items)
  const changes: AdjustmentChange[] = []
  const adjustedItems = cloneItems(input.basePlan.items)

  if (!input.hasSafetyStopSignal && input.intent === 'more_challenging') {
    const confirmedCount = input.basePlan.items.filter(
      (item) => item.confirmationStatus !== 'pending',
    ).length
    const maximumChangedActions = input.trainingExperience === 'beginner'
      ? Math.floor(confirmedCount / 2)
      : Number.POSITIVE_INFINITY
    const ordered = input.basePlan.items
      .map((item, index) => ({
        item,
        index,
        feedback: latestFeedback(input.signals, item.id),
      }))
      .sort((left, right) => (
        Number(right.feedback === 'too_easy') - Number(left.feedback === 'too_easy')
        || left.index - right.index
      ))
    let changedActionCount = 0
    for (const { item, feedback } of ordered) {
      if (changedActionCount >= maximumChangedActions) break
      const change = challengingChange(item, feedback)
      if (!change) continue
      applyChange(adjustedItems, change)
      const candidateSeconds = estimateSeconds(adjustedItems)
      const ratio = estimatedSecondsBefore
        ? (candidateSeconds - estimatedSecondsBefore) / estimatedSecondsBefore
        : 0
      if (ratio > 0.2) {
        const rollback = cloneItems(input.basePlan.items)
        for (const accepted of changes) applyChange(rollback, accepted)
        adjustedItems.splice(0, adjustedItems.length, ...rollback)
        continue
      }
      changes.push(change)
      changedActionCount += 1
    }
  }
  if (!input.hasSafetyStopSignal && input.intent === 'easier_to_finish') {
    for (const item of input.basePlan.items) {
      const itemChanges = easierChanges(item, input)
      if (!itemChanges.length) continue
      const beforeItemChanges = cloneItems(adjustedItems)
      for (const change of itemChanges) applyChange(adjustedItems, change)
      const candidateSeconds = estimateSeconds(adjustedItems)
      const ratio = estimatedSecondsBefore
        ? (candidateSeconds - estimatedSecondsBefore) / estimatedSecondsBefore
        : 0
      if (Math.abs(ratio) > 0.2) {
        adjustedItems.splice(0, adjustedItems.length, ...beforeItemChanges)
        continue
      }
      changes.push(...itemChanges)
    }
  }
  if (!input.hasSafetyStopSignal && input.intent === 'shorter_session') {
    const ordered = [...input.basePlan.items].sort((left, right) => (
      estimateItemSeconds(right) - estimateItemSeconds(left)
      || left.id.localeCompare(right.id)
    ))
    for (const item of ordered) {
      if (latestFeedback(input.signals, item.id) === 'too_easy') continue
      if (estimatedSecondsBefore) {
        const currentRatio = (estimateSeconds(adjustedItems) - estimatedSecondsBefore)
          / estimatedSecondsBefore
        if (currentRatio <= -0.1) break
      }
      for (const change of shorteningCandidates(item)) {
        const beforeChange = cloneItems(adjustedItems)
        applyChange(adjustedItems, change)
        const candidateRatio = estimatedSecondsBefore
          ? (estimateSeconds(adjustedItems) - estimatedSecondsBefore) / estimatedSecondsBefore
          : 0
        if (candidateRatio < -0.2) {
          adjustedItems.splice(0, adjustedItems.length, ...beforeChange)
          continue
        }
        changes.push(change)
        break
      }
    }
  }

  const estimatedSecondsAfter = estimateSeconds(adjustedItems)
  const deltaRatio = estimatedSecondsBefore
    ? (estimatedSecondsAfter - estimatedSecondsBefore) / estimatedSecondsBefore
    : 0
  return {
    id: `${ADJUSTMENT_POLICY_VERSION}:${inputFingerprint}`,
    policyVersion: ADJUSTMENT_POLICY_VERSION,
    basePlanRevision: input.basePlan.revision ?? 0,
    basePlanFingerprint: fingerprint,
    inputFingerprint,
    contextRevision: input.contextRevision,
    intent: input.intent,
    status: changes.length ? 'ready' : 'no_change',
    changes,
    impact: {
      estimatedSecondsBefore,
      estimatedSecondsAfter,
      deltaRatio,
    },
    generatedAt: input.generatedAt,
  }
}

export const fingerprintDraftPlan = planFingerprint
