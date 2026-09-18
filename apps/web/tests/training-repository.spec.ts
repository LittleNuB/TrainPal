import 'fake-indexeddb/auto'

import Dexie from 'dexie'
import { afterEach, describe, expect, it } from 'vitest'

import { createHachimiDatabase } from '@/db/hachimi-database'
import { createDexieTrainingRepository } from '@/db/training-repository'
import type { TrainingRecord, TrainingSession } from '@/domain/training'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'

const databases: string[] = []

const createRepository = () => {
  const name = `hachimi-fitness-training-${crypto.randomUUID()}`
  databases.push(name)
  const database = createHachimiDatabase(name)
  return { database, repository: createDexieTrainingRepository(database) }
}

const session = (sessionId: string, revision = 0): TrainingSession => ({
  id: 'current',
  sessionId,
  revision,
  status: 'paused',
  pauseReason: 'before_start',
  plan: { name: '测试方案', source: 'draft', sourcePlanId: null, items: [] },
  currentItemIndex: 0,
  currentSetIndex: 0,
  currentSetActiveMilliseconds: 0,
  activeStartedAt: null,
  restStartedAt: null,
  restEndsAt: null,
  scheduledRestSeconds: null,
  creditedRestMilliseconds: 0,
  progress: [],
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  updatedAt: '2026-07-21T00:00:00.000Z',
})

const record = (sessionId: string): TrainingRecord => ({
  id: sessionId,
  outcome: 'completed',
  plan: { name: '测试方案', source: 'draft', sourcePlanId: null, items: [] },
  actions: [],
  activeSeconds: 0,
  creditedRestSeconds: 0,
  trainingDurationSeconds: 0,
  completedActionCount: 0,
  calorie: { value: 12, method: 'generic' },
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  endedAt: '2026-07-21T00:01:00.000Z',
})

afterEach(async () => {
  await Promise.all(databases.splice(0).map((name) => Dexie.delete(name)))
})

describe('IndexedDB training repository', () => {
  it.each(['saved', 'draft'])('does not partially save playback when the %s snapshot changed', async (conflicting) => {
    const { database, repository } = createRepository()
    const current = session('playback')
    const item = createQuickExperienceDraftItems()[0]!
    item.sourceRef = { sourceId: 'source' }
    item.segment = { value: { start_seconds: 0, end_seconds: 20 }, source: 'video' }
    current.plan = { name: '方案', source: 'saved', sourcePlanId: 'plan', items: [item] }
    await repository.createCurrent(current)
    const changed = { ...structuredClone(item), playbackSelection: { start_seconds: 3, end_seconds: 5 } }
    const saved = { id: 'plan', name: '方案', items: [conflicting === 'saved' ? changed : item], createdAt: current.startedAt, updatedAt: current.updatedAt }
    const draft = { id: 'current' as const, name: '方案', linkedPlanId: 'plan', items: [conflicting === 'draft' ? changed : item], updatedAt: current.updatedAt }
    await database.plans.put(saved)
    await database.drafts.put(draft)
    const next = structuredClone(current)
    next.revision++
    next.plan.items[0]!.playbackSelection = { start_seconds: 10, end_seconds: 15 }
    const commit = repository.commit({ sessionId: current.sessionId, expectedRevision: 0, nextSession: next, persistPlayback: true })
    if (conflicting === 'saved') expect((await commit).status).toBe('conflict')
    else await expect(commit).rejects.toThrow('draft changed')
    expect(await database.plans.get('plan')).toEqual(saved)
    expect(await database.drafts.get('current')).toEqual(draft)
    expect(await repository.loadCurrent()).toEqual(current)
    database.close()
  })
  it('creates only one current session and rejects a stale revision atomically', async () => {
    const { database, repository } = createRepository()
    const first = session('session-a')
    const second = session('session-b')

    expect(await repository.createCurrent(first)).toEqual({ status: 'created', session: first })
    expect(await repository.createCurrent(second)).toEqual({ status: 'exists', session: first })

    const next = { ...first, revision: 1, status: 'active' as const, pauseReason: null }
    expect(await repository.commit({
      sessionId: first.sessionId,
      expectedRevision: 0,
      nextSession: next,
    })).toEqual({ status: 'committed', session: next, record: null })

    const stale = { ...next, revision: 1, status: 'paused' as const, pauseReason: 'user' as const }
    expect(await repository.commit({
      sessionId: first.sessionId,
      expectedRevision: 0,
      nextSession: stale,
    })).toEqual({ status: 'conflict', session: next })
    expect(await repository.loadCurrent()).toEqual(next)

    database.close()
  })

  it('writes one terminal record and removes current in the same idempotent transaction', async () => {
    const { database, repository } = createRepository()
    const current = session('session-terminal', 4)
    const terminal = record(current.sessionId)
    await repository.createCurrent(current)

    const first = await repository.commit({
      sessionId: current.sessionId,
      expectedRevision: 4,
      record: terminal,
    })
    expect(first).toEqual({ status: 'committed', session: null, record: terminal })
    expect(await repository.loadCurrent()).toBeNull()
    expect(await repository.loadRecord(current.sessionId)).toEqual(terminal)

    const replay = await repository.commit({
      sessionId: current.sessionId,
      expectedRevision: 4,
      record: terminal,
    })
    expect(replay).toEqual({ status: 'already_finalized', record: terminal })
    expect(await database.records.count()).toBe(1)

    database.close()
  })
})
