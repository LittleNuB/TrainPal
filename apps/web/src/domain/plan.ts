import type { DraftItem } from '@/domain/types'

export const normalizeDraftItems = (items: DraftItem[], legacyQuickExperience = false): DraftItem[] => items.map((rawItem) => {
  const { segmentRole: _legacySegmentRole, ...item } = rawItem as DraftItem & { segmentRole?: unknown }
  if (legacyQuickExperience && !item.sourceRef && !item.origin) item.origin = 'quick_experience'
  return JSON.parse(JSON.stringify(item)) as DraftItem
})

export const estimatePlanMinutes = (items: DraftItem[]): number => {
  const seconds = items.reduce((total, item) => {
    const sets = item.sets.value ?? 0
    const activePerSet = item.mode === 'duration'
      ? (item.durationSeconds.value ?? 0)
      : (item.reps.value ?? 0) * 3
    const rest = Math.max(sets - 1, 0) * (item.restSeconds.value ?? 0)
    return total + sets * activePerSet + rest
  }, 0)

  return seconds ? Math.max(1, Math.round(seconds / 60)) : 0
}
