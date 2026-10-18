import { describe, expect, it } from 'vitest'

import {
  QUICK_EXPERIENCE_LABEL,
  createQuickExperienceDraftItems,
} from '@/features/quick-experience/fixture'

describe('quick experience plan', () => {
  it('is explicitly labelled and never masquerades as an AI result', () => {
    let sequence = 0
    const items = createQuickExperienceDraftItems(() => `quick-${++sequence}`)

    expect(QUICK_EXPERIENCE_LABEL).toBe('预设方案')
    expect(items).toHaveLength(3)
    expect(items.every((item) => item.sourceRef === null)).toBe(true)
    expect(items.flatMap((item) => [item.sets.source, item.reps.source, item.durationSeconds.source, item.restSeconds.source])).not.toContain('video')
  })

  it('creates independent copies before placing the sample into the current draft', () => {
    const first = createQuickExperienceDraftItems(() => crypto.randomUUID())
    const second = createQuickExperienceDraftItems(() => crypto.randomUUID())

    first[0]!.name = '已修改'
    expect(second[0]!.name).not.toBe('已修改')
    expect(first.map((item) => item.id)).not.toEqual(second.map((item) => item.id))
  })
})
