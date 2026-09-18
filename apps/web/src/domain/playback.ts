import type { DraftItem, Segment } from '@/domain/types'

export interface PlaybackOption extends Segment { id: string; label: string }

export const sameRange = (left: Segment | null | undefined, right: Segment | null | undefined): boolean =>
  left?.start_seconds === right?.start_seconds && left?.end_seconds === right?.end_seconds

export const playbackOptions = (item: DraftItem): PlaybackOption[] => {
  if (!item.sourceRef || !item.segment.value) return []
  const options = [
    { ...item.segment.value, label: '完整教学参考' },
    ...(item.playbackOptions ?? []),
    ...(item.sourceTips ?? []).map((tip) => ({ ...tip.evidence, label: tip.text })),
  ]
  const duration = item.sourceRef.localMedia?.durationSeconds ?? Infinity
  return options.filter((option) => Number.isFinite(option.start_seconds)
    && Number.isFinite(option.end_seconds) && option.start_seconds >= 0
    && option.end_seconds > option.start_seconds && option.end_seconds <= duration)
    .slice(0, 16).map((option, index) => ({ ...option, id: `clip-${index}` }))
}

export const validSelection = (item: DraftItem, range: Segment | null): boolean =>
  range === null || playbackOptions(item).some((option) => sameRange(option, range))

export const playbackFingerprint = (item: DraftItem): string => JSON.stringify({
  id: item.id, source: item.sourceRef, segment: item.segment,
  selection: item.playbackSelection ?? null, options: playbackOptions(item),
})

export const currentPlaybackRange = (item: DraftItem): Segment | null =>
  item.playbackSelection && validSelection(item, item.playbackSelection)
    ? item.playbackSelection : item.segment.value
