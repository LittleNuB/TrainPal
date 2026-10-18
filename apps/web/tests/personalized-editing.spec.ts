import 'fake-indexeddb/auto'
import Dexie from 'dexie'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { createHachimiDatabase } from '@/db/hachimi-database'
import { createDexieLibraryRepository } from '@/db/library-repository'
import { createDexieDraftRepository } from '@/db/draft-repository'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { createAdjustmentReview } from '@/personalization/adjustment-review'
import { proposePlanAdjustment } from '@/personalization/adjustment-policy'
import { actionFeedbackVersion, matchingFeedback } from '@/personalization/feedback'
import { useDraftStore } from '@/stores/draft'
import type { DraftPlan } from '@/domain/types'
import type { TrainingRecord } from '@/domain/training'

const base = (): DraftPlan => ({ id: 'current', linkedPlanId: 'plan-1', name: '训练',
  items: createQuickExperienceDraftItems(), revision: 0, updatedAt: new Date(0).toISOString() })
const input = { contextRevision: 1, intent: 'more_challenging' as const, trainingExperience: 'intermediate' as const,
  signals: [], hasSafetyStopSignal: false, generatedAt: new Date(0).toISOString() }
beforeEach(() => setActivePinia(createPinia()))

describe('personalized editing v2', () => {
  it('migrates a v5 profile without inventing experience or changing its existing values', async () => {
    const name = `migration-${crypto.randomUUID()}`
    const legacy = new Dexie(name)
    legacy.version(5).stores({ drafts: '&id, updatedAt, linkedPlanId', plans: '&id, updatedAt, createdAt, name',
      sessions: '&id, sessionId, status, updatedAt', records: '&id, endedAt, outcome', profiles: '&id, updatedAt',
      preferences: '&id, updatedAt', localMedia: '&sourceId, importedAt, updatedAt',
      gymtiAttempts: '&id, attemptId, updatedAt', gymtiPendingResults: '&id, resultId, updatedAt', gymtiResults: '&id, resultId, updatedAt' })
    const profile = { id: 'current', sex: null, age: 28, heightCm: null, weightKg: null, updatedAt: input.generatedAt }
    await legacy.table('profiles').put(profile)
    legacy.close()
    const db = createHachimiDatabase(name)
    try {
      expect(await db.profiles.get('current')).toEqual({ ...profile, trainingExperience: null })
      expect(await db.actionFeedback.count()).toBe(0)
    } finally { await db.delete() }
  })

  it('rolls back both AI and reviewed user values together when saving fails', async () => {
    let saved = base(), fail = false
    const draft = useDraftStore()
    await draft.load({ load: async () => structuredClone(saved), save: async plan => {
      if (fail) throw new Error('synthetic disk failure')
      saved = structuredClone(plan)
    } })
    const proposal = draft.proposeAdjustment(input)
    await draft.flushPersist()
    const before = JSON.stringify(draft.items)
    const review = createAdjustmentReview(proposal)
    review.values[0]!.value = 11
    review.values[0]!.edited = true
    fail = true
    expect(await draft.applyAdjustmentProposal(proposal.id, review)).toBe('persist_failed')
    expect(JSON.stringify(draft.items)).toBe(before)
    expect(JSON.stringify(saved.items)).toBe(before)
  })
  it('uses experience, fatigue and available time without exceeding automatic bounds', () => {
    const plan = base()
    const usual = proposePlanAdjustment({ ...input, basePlan: plan })
    const tired = proposePlanAdjustment({ ...input, basePlan: plan, energy: 'tired' })
    const novice = proposePlanAdjustment({ ...input, basePlan: plan, trainingExperience: 'beginner' })
    expect(tired.inputFingerprint).not.toBe(usual.inputFingerprint)
    expect(tired.changes.filter(c => c.field !== 'restSeconds').every(c => c.after.value < c.before.value)).toBe(true)
    expect(new Set(novice.changes.map(c => c.itemId)).size).toBeLessThanOrEqual(1)
    const enough = proposePlanAdjustment({ ...input, basePlan: plan, intent: 'shorter_session', availableMinutes: 30 })
    const short = proposePlanAdjustment({ ...input, basePlan: plan, intent: 'shorter_session', availableMinutes: 1 })
    expect(enough.status).toBe('no_change')
    expect(short.status).toBe('ready')
    expect(Math.abs(short.impact.deltaRatio)).toBeLessThanOrEqual(0.2)
    expect(short.impact.estimatedSecondsAfter).toBeGreaterThan(60)
  })

  it('isolates feedback by plan, action identity and source version', () => {
    const plan = base(), item = plan.items[0]!
    const feedback = { id: 'f', recordId: 'r', planId: 'plan-1', itemId: item.id,
      actionVersion: actionFeedbackVersion(item), value: 'too_hard' as const, recordedAt: input.generatedAt }
    expect(matchingFeedback(plan, [feedback])).toHaveLength(1)
    expect(matchingFeedback({ ...plan, linkedPlanId: 'copy' }, [feedback])).toEqual([])
    item.actionRevision = 1
    expect(matchingFeedback(plan, [feedback])).toEqual([])
  })

  it('stores feedback separately, replaces/clears it and includes it in local data clearing', async () => {
    const db = createHachimiDatabase(`feedback-${crypto.randomUUID()}`)
    try {
      const plan = base(), item = plan.items[0]!
      const record = { id: 'r', endedAt: input.generatedAt, plan: { sourcePlanId: plan.linkedPlanId, items: plan.items },
        actions: [{ itemId: item.id, completedSets: 1 }] } as TrainingRecord
      await db.records.put(record)
      const repo = createDexieLibraryRepository(db)
      const first = await repo.saveActionFeedback('r', item.id, 'too_hard')
      await repo.saveActionFeedback('r', item.id, 'just_right')
      expect((await repo.listActionFeedback()).map(f => f.value)).toEqual(['just_right'])
      expect(await db.records.get('r')).toEqual(record)
      await repo.removeActionFeedback(first.id)
      expect(await repo.listActionFeedback()).toEqual([])
      await repo.saveActionFeedback('r', item.id, 'too_easy')
      await repo.clearAllLocalData()
      expect(await repo.listActionFeedback()).toEqual([])
    } finally { await db.delete() }
  })

  it('reviews without mutating the proposal, atomically applies user overrides, and restores only AI fields', async () => {
    const db = createHachimiDatabase(`review-${crypto.randomUUID()}`)
    try {
      const saved = base()
      await db.plans.put({ ...saved, id: saved.linkedPlanId!, createdAt: saved.updatedAt })
      await db.drafts.put(saved)
      const draft = useDraftStore()
      await draft.load(createDexieDraftRepository(db))
      const proposal = draft.proposeAdjustment(input)
      const original = JSON.stringify(proposal)
      const review = createAdjustmentReview(proposal)
      review.values[0]!.value = 11
      review.values[0]!.edited = true
      expect(JSON.stringify(proposal)).toBe(original)
      expect(draft.items[0]!.durationSeconds.value).toBe(30)
      expect(await draft.applyAdjustmentProposal(proposal.id, review)).toBe('applied')
      const userValue = review.values[0]!
      expect(draft.items.find(item => item.id === userValue.itemId)![userValue.field]).toEqual({ value: 11, source: 'user' })
      expect(draft.items[1]!.reps.source).toBe('personalized')
      await draft.restoreBasePlan()
      expect(draft.items[0]!.durationSeconds).toEqual({ value: 11, source: 'user' })
      expect(draft.items[1]!.reps.value).toBe(12)
      expect((await db.drafts.get('current'))!.items[0]!.durationSeconds.source).toBe('user')
    } finally { await db.delete() }
  })

  it('rejects invalid reviews, stale base plans and legacy proposals', async () => {
    const draft = useDraftStore()
    await draft.load({ load: async () => base(), save: async () => {} })
    let proposal = draft.proposeAdjustment(input)
    const review = createAdjustmentReview(proposal)
    review.values[0]!.value = null
    expect(await draft.applyAdjustmentProposal(proposal.id, review)).toBe('invalid')
    expect(draft.items[0]!.durationSeconds.source).toBe('rule')
    draft.updateValue(draft.items[0]!.id, 'durationSeconds', 21)
    expect(await draft.applyAdjustmentProposal(proposal.id, review)).toBe('conflict')
    proposal = draft.proposeAdjustment(input)
    draft.plan.personalization!.proposal!.policyVersion = 'adjustment-policy.v1'
    expect(await draft.applyAdjustmentProposal(proposal.id)).toBe('conflict')
    await draft.flushPersist()
  })
})
