import Dexie, { type Table, type Transaction } from 'dexie'
import type { ActionFeedback } from '@/personalization/feedback'

import type { DraftPlan, LocalMediaRecord } from '@/domain/types'
import type {
  CurrentGymtiResult,
  GymtiAttempt,
  PendingGymtiResult,
} from '@/domain/gymti'
import type {
  Preferences,
  SavedPlan,
  TrainingProfile,
  TrainingRecord,
  TrainingSession,
} from '@/domain/training'

export class HachimiDatabase extends Dexie {
  actionFeedback!: Table<ActionFeedback, string>
  drafts!: Table<DraftPlan, string>
  plans!: Table<SavedPlan, string>
  sessions!: Table<TrainingSession, string>
  records!: Table<TrainingRecord, string>
  profiles!: Table<TrainingProfile, string>
  preferences!: Table<Preferences, string>
  localMedia!: Table<LocalMediaRecord, string>
  gymtiAttempts!: Table<GymtiAttempt, string>
  gymtiPendingResults!: Table<PendingGymtiResult, string>
  gymtiResults!: Table<CurrentGymtiResult, string>

  constructor(name = 'hachimi-fitness') {
    super(name)

    this.version(1).stores({
      drafts: '&id, updatedAt',
    })

    this.version(2).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
    }).upgrade(async (transaction: Transaction) => {
      await transaction.table('drafts').toCollection().modify((draft: Partial<DraftPlan>) => {
        if (draft.id !== 'current') return
        draft.name = '未命名方案'
        draft.linkedPlanId = null
      })
    })

    this.version(3).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
      localMedia: '&sourceId, importedAt, updatedAt',
    })

    this.version(4).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
      localMedia: '&sourceId, importedAt, updatedAt',
    }).upgrade(async (transaction: Transaction) => {
      const addNullCoachStyle = (record: { coachStyleId?: unknown }): void => {
        if (record.coachStyleId === undefined) record.coachStyleId = null
      }
      await Promise.all([
        transaction.table('preferences').toCollection().modify(addNullCoachStyle),
        transaction.table('sessions').toCollection().modify(addNullCoachStyle),
        transaction.table('records').toCollection().modify(addNullCoachStyle),
      ])
    })

    this.version(5).stores({
      drafts: '&id, updatedAt, linkedPlanId',
      plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt',
      records: '&id, endedAt, outcome',
      profiles: '&id, updatedAt',
      preferences: '&id, updatedAt',
      localMedia: '&sourceId, importedAt, updatedAt',
      gymtiAttempts: '&id, attemptId, updatedAt',
      gymtiPendingResults: '&id, resultId, updatedAt',
      gymtiResults: '&id, resultId, updatedAt',
    })
    this.version(6).stores({ actionFeedback: '&id, recordId, planId, recordedAt' })
      .upgrade(async (transaction: Transaction) => {
        await transaction.table('profiles').toCollection().modify((profile: TrainingProfile) => {
          profile.trainingExperience ??= null
        })
      })
  }
}

export const createHachimiDatabase = (name?: string): HachimiDatabase =>
  new HachimiDatabase(name)

export const database = createHachimiDatabase()
