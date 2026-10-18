import { validateTrainingReminderPlan, type TrainingReminderPlan, type TrainingReminderRequest } from '@/api/reminder-client'
import type { DraftPlan } from '@/domain/types'

export type ReminderPreferences = TrainingReminderRequest['preferences']
export type ReviewedTrainingTip = NonNullable<TrainingReminderRequest['actions'][number]['tips']>[number]
export interface TrainingReminderCache {
  input: TrainingReminderRequest
  plan: TrainingReminderPlan
}

export const reminderInput = (draft: DraftPlan, preferences: ReminderPreferences): TrainingReminderRequest => ({
  plan_id: draft.linkedPlanId ?? draft.id, plan_revision: draft.revision ?? 0,
  preferences: { ...preferences },
  actions: draft.items.map((item) => ({
    action_id: item.id, action_revision: item.actionRevision ?? 0, name: item.name,
    confirmed: item.confirmationStatus === 'confirmed',
    source_id: item.sourceRef?.sourceId ?? null, source_segment: item.segment.value,
    mode: item.mode, sets: item.sets.value ?? 0,
    reps: item.mode === 'reps' ? item.reps.value : null,
    duration_seconds: item.mode === 'duration' ? item.durationSeconds.value : null,
    tips: (item.reviewedSourceTips ?? []).filter((review) => (item.sourceTips ?? []).some((tip) =>
      JSON.stringify(tip) === JSON.stringify(review.tip))),
  })),
})

export const currentReminderCache = async (draft: DraftPlan, preferences?: ReminderPreferences): Promise<TrainingReminderCache | undefined> => {
  const cache = draft.reminderCache
  if (!cache) return undefined
  try {
    const current = reminderInput(draft, preferences ?? cache.input.preferences)
    if (JSON.stringify(current) !== JSON.stringify(cache.input)) return undefined
    await validateTrainingReminderPlan(current, cache.plan)
    if (JSON.stringify(reminderInput(draft, preferences ?? cache.input.preferences)) !== JSON.stringify(cache.input)) return undefined
    return JSON.parse(JSON.stringify(cache)) as TrainingReminderCache
  } catch { return undefined }
}
