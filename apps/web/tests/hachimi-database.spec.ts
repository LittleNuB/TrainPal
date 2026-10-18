import 'fake-indexeddb/auto'

import Dexie from 'dexie'
import { afterEach, describe, expect, it } from 'vitest'

import { createHachimiDatabase } from '@/db/hachimi-database'
import type { DraftPlan } from '@/domain/types'

const databases: string[] = []

const uniqueDatabaseName = (): string => {
  const name = `hachimi-fitness-test-${crypto.randomUUID()}`
  databases.push(name)
  return name
}

afterEach(async () => {
  await Promise.all(databases.splice(0).map((name) => Dexie.delete(name)))
})

describe('Dexie local training data', () => {
  it('upgrades the v1 current draft without changing its actions or field sources', async () => {
    const databaseName = uniqueDatabaseName()
    const legacy = new Dexie(databaseName)
    legacy.version(1).stores({ drafts: '&id, updatedAt' })

    const legacyDraft: Omit<DraftPlan, 'name' | 'linkedPlanId'> = {
      id: 'current',
      items: [
        {
          id: 'video-action',
          name: '拖拽弯举',
          sourceRef: { sourceId: 'video-a', title: '来源视频 A' },
          segment: {
            value: { start_seconds: 41, end_seconds: 51 },
            source: 'video',
          },
          mode: 'reps',
          sets: { value: 3, source: 'rule' },
          reps: { value: 10, source: 'rule' },
          durationSeconds: { value: null, source: null },
          restSeconds: { value: 60, source: 'video' },
          weightKg: { value: null, source: null },
        },
        {
          id: 'manual-action',
          name: '平板支撑',
          sourceRef: null,
          segment: { value: null, source: null },
          mode: 'duration',
          sets: { value: 2, source: 'user' },
          reps: { value: null, source: null },
          durationSeconds: { value: 30, source: 'user' },
          restSeconds: { value: 0, source: 'user' },
          weightKg: { value: null, source: null },
        },
      ],
      updatedAt: '2026-07-20T08:00:00.000Z',
    }
    await legacy.table('drafts').put(legacyDraft)
    legacy.close()

    const database = createHachimiDatabase(databaseName)
    await database.open()

    const migrated = await database.drafts.get('current')
    expect(migrated).toEqual<DraftPlan>({
      ...legacyDraft,
      name: '未命名方案',
      linkedPlanId: null,
    })
    expect(database.tables.map((table) => table.name).sort()).toEqual([
      'actionFeedback',
      'drafts',
      'gymtiAttempts',
      'gymtiPendingResults',
      'gymtiResults',
      'localMedia',
      'plans',
      'preferences',
      'profiles',
      'records',
      'sessions',
    ])

    database.close()
  })

  it('upgrades v2 without losing a draft and can persist a local video Blob', async () => {
    const databaseName = uniqueDatabaseName()
    const legacy = new Dexie(databaseName)
    legacy.version(2).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
    })
    await legacy.table('drafts').put({
      id: 'current',
      name: '本地动作',
      linkedPlanId: null,
      items: [],
      updatedAt: '2026-07-21T16:00:00.000Z',
    })
    legacy.close()

    const database = createHachimiDatabase(databaseName)
    await database.open()
    const blob = new Blob(['video-bytes'], { type: 'video/mp4' })
    await database.localMedia.put({
      sourceId: 'local:11111111-1111-4111-8111-111111111111',
      blob,
      fileName: '训练.mp4',
      mimeType: 'video/mp4',
      sizeBytes: blob.size,
      lastModified: 123,
      durationSeconds: 42,
      importedAt: '2026-07-21T16:00:00.000Z',
      updatedAt: '2026-07-21T16:00:00.000Z',
    })

    expect((await database.drafts.get('current'))?.name).toBe('本地动作')
    const restored = await database.localMedia.get('local:11111111-1111-4111-8111-111111111111')
    expect(restored).toMatchObject({ fileName: '训练.mp4', durationSeconds: 42 })
    expect(restored?.blob).toBeDefined()

    database.close()
  })

  it('upgrades v3 coach data to an explicit null style without rewriting legacy pet ids', async () => {
    const databaseName = uniqueDatabaseName()
    const legacy = new Dexie(databaseName)
    legacy.version(3).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
      localMedia: '&sourceId, importedAt, updatedAt',
    })
    await legacy.table('preferences').put({
      id: 'current',
      petVisible: false,
      updatedAt: '2026-07-21T16:00:00.000Z',
    })
    await legacy.table('sessions').put({
      id: 'current',
      sessionId: 'legacy-session',
      status: 'paused',
      petId: 'hachimi',
      updatedAt: '2026-07-21T16:00:00.000Z',
    })
    await legacy.table('records').put({
      id: 'legacy-record',
      outcome: 'completed',
      petId: 'hachimi',
      endedAt: '2026-07-21T16:10:00.000Z',
    })
    legacy.close()

    const database = createHachimiDatabase(databaseName)
    await database.open()

    expect(await database.preferences.get('current')).toMatchObject({
      petVisible: false,
      coachStyleId: null,
    })
    expect(await database.sessions.get('current')).toMatchObject({
      petId: 'hachimi',
      coachStyleId: null,
    })
    expect(await database.records.get('legacy-record')).toMatchObject({
      petId: 'hachimi',
      coachStyleId: null,
    })

    database.close()
  })

  it('adds the three single-slot GYMTI lifecycle tables without changing v4 data', async () => {
    const databaseName = uniqueDatabaseName()
    const legacy = new Dexie(databaseName)
    legacy.version(4).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
      localMedia: '&sourceId, importedAt, updatedAt',
    })
    await legacy.table('preferences').put({
      id: 'current',
      petVisible: true,
      coachStyleId: 'zen',
      updatedAt: '2026-07-23T00:00:00.000Z',
    })
    legacy.close()

    const database = createHachimiDatabase(databaseName)
    await database.open()

    expect(await database.preferences.get('current')).toMatchObject({ coachStyleId: 'zen' })
    expect(await Promise.all([
      database.gymtiAttempts.count(),
      database.gymtiPendingResults.count(),
      database.gymtiResults.count(),
    ])).toEqual([0, 0, 0])
    database.close()
  })
})
