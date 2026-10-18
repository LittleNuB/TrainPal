import type { DraftItem, DraftPlan } from '@/domain/types'
import type { AdjustmentSignal } from '@/domain/personalization'
import type { TrainingRecord } from '@/domain/training'

export interface ActionFeedback {
  id: string
  recordId: string
  planId: string
  itemId: string
  actionVersion: string
  value: AdjustmentSignal['value']
  recordedAt: string
}

// Parameters can change during adjustment. Identity, source and action revision cannot.
export const actionFeedbackVersion = (item: DraftItem): string => JSON.stringify({
  revision: item.actionRevision ?? 0, name: item.name, mode: item.mode,
  source: item.sourceRef ?? null, segment: item.segment,
})

export const feedbackForRecord = (
  record: TrainingRecord, itemId: string, value: AdjustmentSignal['value'], recordedAt: string,
): ActionFeedback => {
  const item = record.plan.items.find(item => item.id === itemId)
  if (!item || !record.plan.sourcePlanId || !record.actions.some(action => action.itemId === itemId && action.completedSets > 0)) {
    throw new Error('feedback requires a completed action and saved plan identity')
  }
  return { id: `${record.id}:${itemId}`, recordId: record.id, planId: record.plan.sourcePlanId,
    itemId, actionVersion: actionFeedbackVersion(item), value, recordedAt }
}

export const matchingFeedback = (plan: DraftPlan, entries: ActionFeedback[]): AdjustmentSignal[] => entries
  .filter(entry => entry.planId === plan.linkedPlanId && plan.items.some(item =>
    item.id === entry.itemId && actionFeedbackVersion(item) === entry.actionVersion))
  .map(({ itemId, value, recordedAt }) => ({ itemId, value, recordedAt }))
