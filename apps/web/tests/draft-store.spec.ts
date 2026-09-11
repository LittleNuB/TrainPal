import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AnalysisCandidate, DraftPlan, DraftRepository } from '@/domain/types'
import { useDraftStore } from '@/stores/draft'

class MemoryDraftRepository implements DraftRepository {
  value: DraftPlan | undefined
  saveCount = 0

  async load(): Promise<DraftPlan | undefined> {
    return structuredClone(this.value)
  }

  async save(plan: DraftPlan): Promise<void> {
    this.value = structuredClone(plan)
    this.saveCount += 1
  }
}

class RecoverableDraftRepository extends MemoryDraftRepository {
  failSave = true

  override async save(plan: DraftPlan): Promise<void> {
    if (this.failSave) throw new Error('indexeddb unavailable')
    await super.save(plan)
  }
}

class DeferredDraftRepository extends MemoryDraftRepository {
  saveStarted!: () => void
  releaseSave!: () => void
  readonly started = new Promise<void>((resolve) => { this.saveStarted = resolve })
  private readonly released = new Promise<void>((resolve) => { this.releaseSave = resolve })

  override async save(plan: DraftPlan): Promise<void> {
    this.saveStarted()
    await this.released
    await super.save(plan)
  }
}

function candidate(sourceId: string): AnalysisCandidate {
  return {
    id: `${sourceId}-candidate`,
    name: '拖拽弯举',
    source_id: sourceId,
    segment: { start_seconds: 41, end_seconds: 51 },
    parameters: {
      mode: 'reps',
      sets: null,
      reps: null,
      duration_seconds: null,
      rest_seconds: null,
    },
    evidence: [
      { type: 'speech', start_seconds: 42, end_seconds: 49 },
      { type: 'visual', start_seconds: 41, end_seconds: 51 },
    ],
    needs_confirmation: false,
  }
}

describe('方案草稿 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  it('stores local source fingerprints and repeated actions in source order without legacy roles', async () => {
    const store = useDraftStore()
    await store.load(new MemoryDraftRepository())
    const sourceId = 'local:11111111-1111-4111-8111-111111111111'
    const later = candidate(sourceId)
    later.id = 'later'
    later.segment = { start_seconds: 40, end_seconds: 50 }
    const earlier = candidate(sourceId)
    earlier.id = 'earlier'
    earlier.segment = { start_seconds: 10, end_seconds: 20 }

    store.applyCandidateProposal([later, earlier], {
      [sourceId]: {
        title: '训练.mp4',
        origin_url: null,
        kind: 'local',
        localMedia: {
          fileName: '训练.mp4',
          mimeType: 'video/mp4',
          sizeBytes: 123,
          lastModified: 456,
          durationSeconds: 60,
        },
      },
    })

    expect(store.items.map((item) => item.segment.value?.start_seconds)).toEqual([10, 40])
    expect(store.items[0]!.sourceRef).toMatchObject({
      sourceId,
      kind: 'local',
      localMedia: { fileName: '训练.mp4', sizeBytes: 123 },
    })
    expect(store.items.every((item) => !('segmentRole' in item))).toBe(true)
  })

  it('applies visible rule defaults and persists actions from multiple videos', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)

    store.applyCandidateProposal([candidate('video-a'), candidate('video-b')], {
      'video-a': {
        title: '来源视频 A',
        origin_url: 'https://www.douyin.com/video/123456',
      },
      'video-b': {
        title: '来源视频 B',
        origin_url: null,
      },
    })

    expect(store.items).toHaveLength(2)
    expect(store.items.map((item) => item.sourceRef?.sourceId)).toEqual(['video-a', 'video-b'])
    expect(store.items.map((item) => item.sourceRef?.title)).toEqual(['来源视频 A', '来源视频 B'])
    expect(store.items.map((item) => item.sourceRef?.originUrl)).toEqual([
      'https://www.douyin.com/video/123456',
      undefined,
    ])
    expect(store.items[0].sets).toEqual({ value: 3, source: 'rule' })
    expect(store.items[0].reps).toEqual({ value: 10, source: 'rule' })
    expect(store.items[0].restSeconds).toEqual({ value: 60, source: 'rule' })
    expect(store.items[0].weightKg).toEqual({ value: null, source: null })

    store.updateValue(store.items[0].id, 'sets', 4)
    expect(store.items[0].sets).toEqual({ value: 4, source: 'user' })

    await vi.advanceTimersByTimeAsync(301)
    expect(repository.saveCount).toBe(1)
    expect(repository.value?.items).toHaveLength(2)
  })

  it('keeps uncertain candidates pending and uses an editable rule placeholder when mode is missing', async () => {
    const store = useDraftStore()
    await store.load(new MemoryDraftRepository())
    const uncertain = candidate('video-a')
    uncertain.parameters.mode = null
    uncertain.parameters.reps = null
    uncertain.needs_confirmation = false

    store.applyCandidateProposal([uncertain], {}, 'replace')

    expect(store.items[0]).toMatchObject({
      mode: 'reps',
      confirmationStatus: 'pending',
      reps: { value: 10, source: 'rule' },
    })
    store.updateMode(store.items[0]!.id, 'duration')
    store.confirmItem(store.items[0]!.id)
    expect(store.items[0]).toMatchObject({
      mode: 'duration',
      confirmationStatus: 'confirmed',
      durationSeconds: { value: 30, source: 'rule' },
    })
  })

  it('requires an explicit append or replace strategy for a proposal', async () => {
    const store = useDraftStore()
    await store.load(new MemoryDraftRepository())
    store.addManualAction({ name: '已有动作', mode: 'reps' })

    store.applyCandidateProposal([candidate('video-a')], {}, 'append')
    expect(store.items.map((item) => item.name)).toEqual(['已有动作', '拖拽弯举'])

    store.applyCandidateProposal([candidate('video-b')], {}, 'replace')
    expect(store.plan.name).toBe('未命名方案')
    expect(store.plan.linkedPlanId).toBeNull()
    expect(store.items.map((item) => item.sourceRef?.sourceId)).toEqual(['video-b'])
  })

  it('supports manual actions, duplicate, reorder, delete, and reload', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)

    store.addManualAction({ name: '平板支撑', mode: 'duration' })
    const originalId = store.items[0].id
    expect(store.items[0].sourceRef).toBeNull()
    expect(store.items[0].durationSeconds).toEqual({ value: 30, source: 'rule' })

    store.duplicate(originalId)
    expect(store.items).toHaveLength(2)
    expect(store.items[1].id).not.toBe(originalId)
    store.move(store.items[1].id, -1)
    expect(store.items[0].id).not.toBe(originalId)
    store.remove(originalId)
    await store.flushPersist()

    setActivePinia(createPinia())
    const reloaded = useDraftStore()
    await reloaded.load(repository)
    expect(reloaded.items).toHaveLength(1)
    expect(reloaded.items[0].name).toBe('平板支撑（副本）')
  })

  it('persists an empty edited action name so plan validation can block training', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)
    store.addManualAction({ name: '平板支撑', mode: 'duration' })

    store.updateName(store.items[0]!.id, '   ')
    await vi.advanceTimersByTimeAsync(301)

    expect(store.items[0]!.name).toBe('')
    expect(repository.value?.items[0]?.name).toBe('')
  })

  it('does not persist a non-http original-video URL', async () => {
    const store = useDraftStore()
    await store.load(new MemoryDraftRepository())

    store.applyCandidateProposal([candidate('video-a')], {
      'video-a': { title: '来源视频 A', origin_url: 'javascript:alert(1)' },
    })

    expect(store.items[0]!.sourceRef).toEqual({
      sourceId: 'video-a',
      title: '来源视频 A',
      originUrl: undefined,
    })
  })

  it('adopts an already-persisted library draft without writing it again', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)

    store.adoptPersistedPlan({
      id: 'current',
      name: '已存方案',
      linkedPlanId: 'plan-a',
      items: [],
      updatedAt: '2026-07-21T00:00:00.000Z',
    })

    expect(store.plan.name).toBe('已存方案')
    expect(store.plan.linkedPlanId).toBe('plan-a')
    expect(repository.saveCount).toBe(0)
  })

  it('reports an automatic-save failure and lets the user retry it', async () => {
    const repository = new RecoverableDraftRepository()
    const store = useDraftStore()
    await store.load(repository)

    store.addManualAction({ name: '平板支撑', mode: 'duration' })
    expect(store.persistState).toBe('pending')

    await vi.advanceTimersByTimeAsync(301)
    expect(store.persistState).toBe('failed')
    expect(store.persistMessage).toBe('未保存，点击重试')

    repository.failSave = false
    await store.retryPersist()

    expect(store.persistState).toBe('saved')
    expect(store.persistMessage).toBe('已自动保存到本机')
    expect(repository.value?.items[0]?.name).toBe('平板支撑')
  })

  it('cancels a pending debounce before local data is cleared', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)
    store.addManualAction({ name: '平板支撑', mode: 'duration' })

    await store.quiescePersistence()
    await vi.advanceTimersByTimeAsync(301)

    expect(repository.saveCount).toBe(0)
  })

  it('waits for an in-flight draft write before local data is cleared', async () => {
    const repository = new DeferredDraftRepository()
    const store = useDraftStore()
    await store.load(repository)
    store.addManualAction({ name: '平板支撑', mode: 'duration' })
    await vi.advanceTimersByTimeAsync(301)
    await repository.started
    let quiesced = false

    const waiting = store.quiescePersistence().then(() => { quiesced = true })
    await Promise.resolve()
    expect(quiesced).toBe(false)

    repository.releaseSave()
    await waiting
    expect(quiesced).toBe(true)
    expect(repository.saveCount).toBe(1)
    expect(store.persistState).toBe('saved')
  })

  it('persists, applies, and restores a fresh adjustment proposal without overriding user edits', async () => {
    const repository = new MemoryDraftRepository()
    const store = useDraftStore()
    await store.load(repository)
    store.applyCandidateProposal([candidate('video-a'), candidate('video-b')], {}, 'replace')

    const proposal = store.proposeAdjustment({
      contextRevision: 8,
      intent: 'more_challenging',
      trainingExperience: 'intermediate',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T02:00:00.000Z',
    })
    expect(proposal.status).toBe('ready')
    expect(store.adjustmentProposal?.id).toBe(proposal.id)
    await vi.advanceTimersByTimeAsync(301)
    expect(repository.value?.personalization?.proposal?.id).toBe(proposal.id)

    expect(await store.applyAdjustmentProposal(proposal.id)).toBe('applied')
    expect(store.items.map((item) => item.reps)).toEqual([
      { value: 12, source: 'personalized' },
      { value: 12, source: 'personalized' },
    ])

    store.updateValue(store.items[0]!.id, 'reps', 14)
    expect(await store.restoreBasePlan()).toEqual({ status: 'restored', count: 1 })
    expect(store.items.map((item) => item.reps)).toEqual([
      { value: 14, source: 'user' },
      { value: 10, source: 'rule' },
    ])
  })

  it('reuses an identical saved proposal and invalidates it when the plan changes', async () => {
    const store = useDraftStore()
    await store.load(new MemoryDraftRepository())
    store.addManualAction({ name: '深蹲', mode: 'reps' })
    const input = {
      contextRevision: 9,
      intent: 'more_challenging' as const,
      trainingExperience: 'intermediate' as const,
      signals: [],
      hasSafetyStopSignal: false,
    }
    const first = store.proposeAdjustment({
      ...input,
      generatedAt: '2026-08-24T02:10:00.000Z',
    })
    const repeated = store.proposeAdjustment({
      ...input,
      generatedAt: '2026-08-24T02:20:00.000Z',
    })

    expect(repeated).toEqual(first)
    const safetyStop = store.proposeAdjustment({
      ...input,
      hasSafetyStopSignal: true,
      generatedAt: '2026-08-24T02:25:00.000Z',
    })
    expect(safetyStop.id).not.toBe(first.id)
    expect(safetyStop).toMatchObject({ status: 'no_change', changes: [] })
    store.updatePlanName('已经变化的方案')
    expect(store.adjustmentProposal).toBeNull()
    expect(await store.applyAdjustmentProposal(first.id)).toBe('conflict')
  })

  it('rolls back an adjustment when its immediate persistence fails', async () => {
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const store = useDraftStore()
    await store.load(repository)
    store.addManualAction({ name: '深蹲', mode: 'reps' })
    const proposal = store.proposeAdjustment({
      contextRevision: 10,
      intent: 'more_challenging',
      trainingExperience: 'intermediate',
      signals: [],
      hasSafetyStopSignal: false,
      generatedAt: '2026-08-24T02:30:00.000Z',
    })
    await store.flushPersist()
    repository.failSave = true

    expect(await store.applyAdjustmentProposal(proposal.id)).toBe('persist_failed')
    expect(store.items[0]!.reps).toEqual({ value: 10, source: 'rule' })
    expect(store.adjustmentProposal?.id).toBe(proposal.id)
    expect(store.persistState).toBe('failed')
  })
})
