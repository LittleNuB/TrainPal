import { describe, expect, it } from 'vitest'

import type { DraftItem, DraftPlan, SourcedValue } from '@/domain/types'
import { proposePlanAdjustment } from '@/personalization/adjustment-policy'

const sourced = (value: number | null, source: SourcedValue<number>['source']): SourcedValue<number> => ({
  value,
  source,
})

const repsItem = (overrides: Partial<DraftItem> = {}): DraftItem => ({
  id: 'action-1',
  name: '深蹲',
  sourceRef: null,
  segment: { value: null, source: null },
  confirmationStatus: 'confirmed',
  mode: 'reps',
  sets: sourced(3, 'rule'),
  reps: sourced(10, 'rule'),
  durationSeconds: sourced(null, null),
  restSeconds: sourced(60, 'rule'),
  weightKg: sourced(null, null),
  ...overrides,
})

const plan = (items: DraftItem[]): DraftPlan => ({
  id: 'current',
  name: '今天的训练',
  linkedPlanId: null,
  items,
  updatedAt: '2026-08-24T00:00:00.000Z',
})

describe('AdjustmentPolicy.v1', () => {
  it.each([
    ['more_challenging', 'reps', 8, 9],
    ['easier_to_finish', 'reps', 8, 7],
    ['shorter_session', 'reps', 8, 7],
    ['more_challenging', 'duration', 15, null],
    ['easier_to_finish', 'duration', 15, null],
    ['shorter_session', 'duration', 15, null],
    ['more_challenging', 'duration', 40, 45],
    ['easier_to_finish', 'duration', 40, 35],
  ] as const)('keeps the per-action cap for %s / %s / %s', (intent, mode, value, expected) => {
    const proposal = proposePlanAdjustment({
      basePlan: plan([
        repsItem({
          mode,
          sets: sourced(3, 'video'),
          reps: sourced(mode === 'reps' ? value : null, mode === 'reps' ? 'rule' : null),
          durationSeconds: sourced(mode === 'duration' ? value : null, mode === 'duration' ? 'rule' : null),
        }),
        repsItem({ id: 'locked', reps: sourced(20, 'video'), sets: sourced(4, 'video') }),
      ]),
      contextRevision: 1,
      intent,
      trainingExperience: null,
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-09-11T15:00:00.000Z',
    })
    if (expected === null) {
      expect(proposal.changes).toEqual([])
    } else {
      expect(proposal.changes).toEqual([
        expect.objectContaining({ itemId: 'action-1', after: { value: expected, source: 'personalized' } }),
      ])
    }
  })

  it('increases one eligible repetitions field without touching locked values', () => {
    const proposal = proposePlanAdjustment({
      basePlan: plan([
        repsItem(),
        repsItem({
          id: 'video-action',
          reps: sourced(12, 'video'),
          restSeconds: sourced(45, 'user'),
        }),
      ]),
      contextRevision: 3,
      intent: 'more_challenging',
      trainingExperience: 'intermediate',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:00:00.000Z',
    })

    expect(proposal.status).toBe('ready')
    expect(proposal.changes).toEqual([
      expect.objectContaining({
        itemId: 'action-1',
        field: 'reps',
        before: { value: 10, source: 'rule' },
        after: { value: 12, source: 'personalized' },
        reasonCode: 'increase_for_challenge',
      }),
    ])
    expect(proposal.impact.deltaRatio).toBeLessThanOrEqual(0.2)
  })

  it('reduces duration and extends recovery for an easier session', () => {
    const proposal = proposePlanAdjustment({
      basePlan: plan([repsItem({
        mode: 'duration',
        reps: sourced(null, null),
        durationSeconds: sourced(30, 'rule'),
      })]),
      contextRevision: 4,
      intent: 'easier_to_finish',
      trainingExperience: 'beginner',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:05:00.000Z',
    })

    expect(proposal.changes).toEqual([
      expect.objectContaining({
        field: 'durationSeconds',
        before: { value: 30, source: 'rule' },
        after: { value: 25, source: 'personalized' },
        reasonCode: 'reduce_to_finish',
      }),
      expect.objectContaining({
        field: 'restSeconds',
        before: { value: 60, source: 'rule' },
        after: { value: 75, source: 'personalized' },
        reasonCode: 'extend_recovery',
      }),
    ])
  })

  it('shortens the longest eligible plan without exceeding the plan cap', () => {
    const proposal = proposePlanAdjustment({
      basePlan: plan([
        repsItem({ id: 'longest', reps: sourced(15, 'rule') }),
        repsItem({ id: 'second' }),
        repsItem({ id: 'third' }),
      ]),
      contextRevision: 5,
      intent: 'shorter_session',
      trainingExperience: null,
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:10:00.000Z',
    })

    expect(proposal.changes).toEqual([
      expect.objectContaining({
        itemId: 'longest',
        field: 'sets',
        before: { value: 3, source: 'rule' },
        after: { value: 2, source: 'personalized' },
        reasonCode: 'shorten_time_budget',
      }),
    ])
    expect(proposal.impact.deltaRatio).toBeLessThanOrEqual(-0.1)
    expect(proposal.impact.deltaRatio).toBeGreaterThanOrEqual(-0.2)
  })

  it('does not override a just-right signal or any safety stop', () => {
    const basePlan = plan([repsItem()])
    const justRight = proposePlanAdjustment({
      basePlan,
      contextRevision: 6,
      intent: 'more_challenging',
      trainingExperience: 'advanced',
      signals: [{
        itemId: 'action-1',
        value: 'just_right',
        recordedAt: '2026-08-24T00:30:00.000Z',
      }],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:15:00.000Z',
    })
    const safetyStop = proposePlanAdjustment({
      basePlan,
      contextRevision: 6,
      intent: 'easier_to_finish',
      trainingExperience: 'beginner',
      signals: [],
      hasSafetyStopSignal: true,
      generatedAt: '2026-08-24T01:15:00.000Z',
    })

    expect(justRight).toMatchObject({ status: 'no_change', changes: [] })
    expect(safetyStop).toMatchObject({ status: 'no_change', changes: [] })
  })

  it('limits a beginner challenge proposal to half of the confirmed actions', () => {
    const proposal = proposePlanAdjustment({
      basePlan: plan([
        repsItem({ id: 'one' }),
        repsItem({ id: 'two' }),
        repsItem({ id: 'three' }),
        repsItem({ id: 'four' }),
      ]),
      contextRevision: 7,
      intent: 'more_challenging',
      trainingExperience: 'beginner',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:20:00.000Z',
    })

    expect(proposal.changes).toHaveLength(2)
    expect(proposal.changes.map((change) => change.itemId)).toEqual(['one', 'two'])
  })

  it('rounds the beginner challenge limit down for an odd action count', () => {
    const proposal = proposePlanAdjustment({
      basePlan: plan([
        repsItem({ id: 'one' }),
        repsItem({ id: 'two' }),
        repsItem({ id: 'three' }),
      ]),
      contextRevision: 8,
      intent: 'more_challenging',
      trainingExperience: 'beginner',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:25:00.000Z',
    })

    expect(proposal.changes.map((change) => change.itemId)).toEqual(['one'])
  })

  it('does not shorten an action whose latest feedback says it was too easy', () => {
    const proposal = proposePlanAdjustment({
      basePlan: plan([repsItem()]),
      contextRevision: 9,
      intent: 'shorter_session',
      trainingExperience: null,
      signals: [{
        itemId: 'action-1',
        value: 'too_easy',
        recordedAt: '2026-08-24T00:40:00.000Z',
      }],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:30:00.000Z',
    })

    expect(proposal).toMatchObject({ status: 'no_change', changes: [] })
  })

  it('rejects out-of-envelope source values instead of clamping them backwards', () => {
    const challenge = proposePlanAdjustment({
      basePlan: plan([repsItem({ reps: sourced(30, 'rule') })]),
      contextRevision: 10,
      intent: 'more_challenging',
      trainingExperience: 'advanced',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:35:00.000Z',
    })
    const easier = proposePlanAdjustment({
      basePlan: plan([repsItem({ restSeconds: sourced(10, 'rule') })]),
      contextRevision: 10,
      intent: 'easier_to_finish',
      trainingExperience: 'beginner',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T01:35:00.000Z',
    })

    expect(challenge).toMatchObject({ status: 'no_change', changes: [] })
    expect(easier.changes.some((change) => change.field === 'restSeconds')).toBe(false)
  })
})
