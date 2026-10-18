import 'fake-indexeddb/auto'

import { webcrypto } from 'node:crypto'
import Dexie from 'dexie'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { fingerprintTrainingReminderInput } from '@/api/reminder-fingerprint'
import { createDexieDraftRepository } from '@/db/draft-repository'
import { createHachimiDatabase, type HachimiDatabase } from '@/db/hachimi-database'
import { createDexieLibraryRepository } from '@/db/library-repository'
import { useDraftStore } from '@/stores/draft'
import { useTrainingStore } from '@/stores/training'
import { createDexieTrainingRepository } from '@/db/training-repository'
import { createTrainingEngine } from '@/training/training-engine'
import { estimateCalories } from '@/features/experience/calorie'

const databases: HachimiDatabase[] = []
const preferences = { automatic_speech: true, confirmed: true }

const setup = async () => {
  setActivePinia(createPinia())
  const db = createHachimiDatabase(`synthetic-reminder-${crypto.randomUUID()}`)
  databases.push(db)
  const repository = createDexieDraftRepository(db)
  const draft = useDraftStore()
  await draft.load(repository)
  draft.applyCandidateProposal([{
    id: 'synthetic-candidate', name: '深蹲', source_id: 'synthetic-source',
    segment: { start_seconds: 0, end_seconds: 20 },
    parameters: { mode: 'duration', sets: 1, reps: null, duration_seconds: 15, rest_seconds: 0 },
    evidence: [], needs_confirmation: false,
    tips: [{ text: '起身时呼气', category: 'breathing',
      evidence: { type: 'speech', start_seconds: 5, end_seconds: 8 } }],
  }])
  await draft.flushPersist()
  return { db, repository, draft }
}

const respond = async (body: string) => {
  const input = JSON.parse(body)
  const action = input.actions[0]
  return new Response(JSON.stringify({
    plan_id: input.plan_id, plan_revision: input.plan_revision,
    input_fingerprint: await fingerprintTrainingReminderInput(input),
    contract_version: 'training-reminders.v1.1', skill_version: '1.0.0', policy_version: 'ReminderPolicy.v1',
    mode: 'model', fallback_reason: null,
    reminders: [{ action_id: action.action_id, action_revision: action.action_revision,
      tip_id: action.tips[0].tip_id, stage: 'training', trigger: 'set_start',
      set_number: 1, active_seconds: null, priority: 1, max_plays: 1 }],
    budgets: [{ action_id: action.action_id, utterance_limit: 1, active_interval_seconds: 30, end_quiet_seconds: 10 }],
  }))
}

beforeEach(() => vi.stubGlobal('crypto', webcrypto))
afterEach(async () => {
  vi.unstubAllGlobals()
  for (const db of databases.splice(0)) { db.close(); await Dexie.delete(db.name) }
})

it('keeps an explicitly reviewed arrangement across archive, refresh and reopening without another HTTP request', async () => {
  const { db, repository, draft } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_, options) => respond(options!.body as string))
  vi.stubGlobal('fetch', fetcher)
  expect(await draft.prepareTrainingReminders(preferences, new AbortController().signal)).toBe(true)
  await draft.flushPersist()
  const cache = structuredClone(JSON.parse(JSON.stringify(draft.plan.reminderCache)))
  setActivePinia(createPinia())
  const refreshed = useDraftStore()
  await refreshed.load(repository)
  expect(refreshed.plan.reminderCache).toEqual(cache)
  expect(await refreshed.prepareTrainingReminders(preferences, new AbortController().signal)).toBe(true)
  expect(fetcher).toHaveBeenCalledTimes(1)
  const reopened = await createDexieLibraryRepository(db).openPlan(refreshed.plan.linkedPlanId!)
  expect(reopened.reminderCache).toEqual(cache)
})

it('freezes the arrangement for the session while editing invalidates only the next workout', async () => {
  const { db, draft } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => respond(options!.body as string)))
  await draft.prepareTrainingReminders(preferences, new AbortController().signal)
  const training = useTrainingStore()
  const engine = createTrainingEngine({ persistence: createDexieTrainingRepository(db),
    estimateCalories: (input) => { const result = estimateCalories(input); return { value: result.kcal, method: result.method } } })
  await training.load(engine)
  expect((await training.createFromDraft(draft.plan)).ok).toBe(true)
  expect(training.session?.plan.reminderCache?.plan.mode).toBe('model')
  draft.updateValue(draft.items[0]!.id, 'durationSeconds', 60)
  await draft.flushPersist()
  expect(draft.plan.reminderCache).toBeUndefined()
  expect(training.session?.plan.reminderCache?.input.actions[0]?.duration_seconds).toBe(15)
  await training.restore()
  expect(training.session?.plan.reminderCache?.input.actions[0]?.duration_seconds).toBe(15)
  await training.endEarly()
  expect(training.lastRecord?.plan.reminderCache?.input.actions[0]?.duration_seconds).toBe(15)
})

it('discards a late HTTP result after a plan edit instead of caching it for a new workout', async () => {
  const { draft } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  let finish!: (response: Response) => void
  let entered!: () => void
  const started = new Promise<void>((resolve) => { entered = resolve })
  let body = ''
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => {
    body = options!.body as string
    entered()
    return new Promise<Response>((resolve) => { finish = resolve })
  }))
  const pending = draft.prepareTrainingReminders(preferences, new AbortController().signal)
  await started
  draft.updateValue(draft.items[0]!.id, 'sets', 2)
  finish(await respond(body))
  expect(await pending).toBe(false)
  expect(draft.plan.reminderCache).toBeUndefined()
  await draft.flushPersist()
})

it('does not carry a tip approval across a changed action identity or a copied action', async () => {
  const { draft } = await setup()
  const itemId = draft.items[0]!.id
  draft.reviewSourceTips(itemId, [0])
  draft.duplicate(itemId)
  expect(draft.items[1]!.reviewedSourceTips).toBeUndefined()
  draft.updateName(itemId, '站姿提踵')
  expect(draft.items[0]!.reviewedSourceTips).toBeUndefined()
  expect(draft.items[0]!.actionRevision).toBe(1)
  await draft.flushPersist()
})

it('does not report an old cached arrangement ready after switching plans during validation', async () => {
  const { draft } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => respond(options!.body as string)))
  expect(await draft.prepareTrainingReminders(preferences, new AbortController().signal)).toBe(true)
  const pending = draft.prepareTrainingReminders(preferences, new AbortController().signal)
  draft.adoptPersistedPlan({ ...JSON.parse(JSON.stringify(draft.plan)),
    linkedPlanId: null, reminderCache: undefined })
  expect(await pending).toBe(false)
  expect(draft.plan.reminderCache).toBeUndefined()
  await draft.flushPersist()
})

it.each(['load', 'reload'] as const)('does not restore old reminders after clearing the draft during %s validation', async (operation) => {
  const { repository, draft } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => respond(options!.body as string)))
  await draft.prepareTrainingReminders(preferences, new AbortController().signal)
  await draft.flushPersist()
  let entered!: () => void
  let release!: () => void
  const started = new Promise<void>((resolve) => { entered = resolve })
  const gate = new Promise<void>((resolve) => { release = resolve })
  // Delay only the system cryptography boundary; calculate the real digest.
  vi.stubGlobal('crypto', { randomUUID: () => webcrypto.randomUUID(), subtle: {
    digest: async (...args: Parameters<typeof webcrypto.subtle.digest>) => {
      entered(); await gate
      return webcrypto.subtle.digest(...args)
    },
  } })
  const pending = operation === 'load' ? draft.load(repository) : draft.reload()
  await started
  draft.resetLocalState()
  release()
  await pending
  expect(draft.items).toEqual([])
  expect(draft.plan.reminderCache).toBeUndefined()
  expect(draft.persistState).toBe('idle')
})

it.each(['wrong-fingerprint', 'old-contract'])('reads the existing plan but discards a %s arrangement', async (failure) => {
  const { repository, draft } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => respond(options!.body as string)))
  await draft.prepareTrainingReminders(preferences, new AbortController().signal)
  await draft.flushPersist()
  const saved = JSON.parse(JSON.stringify(draft.plan))
  if (failure === 'wrong-fingerprint') saved.reminderCache.plan.input_fingerprint = 'b'.repeat(64)
  else saved.reminderCache.plan.contract_version = 'training-reminders.v1'
  await repository.save(saved)
  setActivePinia(createPinia())
  const refreshed = useDraftStore()
  await refreshed.load(repository)
  expect(refreshed.plan.reminderCache).toBeUndefined()
  expect(refreshed.items[0]!.sourceTips).toEqual(draft.items[0]!.sourceTips)
  expect(refreshed.items[0]!.reviewedSourceTips).toEqual(draft.items[0]!.reviewedSourceTips)
})
