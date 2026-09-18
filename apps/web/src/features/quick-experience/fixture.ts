import type { ActionMode, DraftItem, SourcedValue } from '@/domain/types'

export const QUICK_EXPERIENCE_LABEL = '快速体验方案'
export const QUICK_EXPERIENCE_PLAN_NAME = '8 分钟手臂唤醒'

type IdFactory = () => string

interface QuickActionDefinition {
  name: string
  mode: ActionMode
  sets: number
  reps?: number
  durationSeconds?: number
  restSeconds: number
}

const QUICK_ACTIONS: readonly QuickActionDefinition[] = [
  { name: '肩部绕环', mode: 'duration', sets: 2, durationSeconds: 30, restSeconds: 30 },
  { name: '站姿弯举', mode: 'reps', sets: 3, reps: 12, restSeconds: 45 },
  { name: '窄距俯卧撑', mode: 'reps', sets: 3, reps: 8, restSeconds: 60 },
]

const ruleValue = (value: number | null): SourcedValue<number> => ({
  value,
  source: value === null ? null : 'rule',
})

export function createQuickExperienceDraftItems(
  createId: IdFactory = () => crypto.randomUUID(),
): DraftItem[] {
  return QUICK_ACTIONS.map((action) => ({
    id: createId(),
    name: action.name,
    origin: 'quick_experience',
    sourceRef: null,
    segment: { value: null, source: null },
    mode: action.mode,
    sets: ruleValue(action.sets),
    reps: ruleValue(action.reps ?? null),
    durationSeconds: ruleValue(action.durationSeconds ?? null),
    restSeconds: ruleValue(action.restSeconds),
    weightKg: { value: null, source: null },
  }))
}
