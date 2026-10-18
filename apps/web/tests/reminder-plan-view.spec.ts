import 'fake-indexeddb/auto'
import { webcrypto } from 'node:crypto'
import Dexie from 'dexie'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { fingerprintTrainingReminderInput } from '@/api/reminder-fingerprint'
import { createDexieDraftRepository } from '@/db/draft-repository'
import { createHachimiDatabase, type HachimiDatabase } from '@/db/hachimi-database'
import { createDexieLibraryRepository } from '@/db/library-repository'
import { createDexieTrainingRepository } from '@/db/training-repository'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'
import { createTrainingEngine } from '@/training/training-engine'
import { estimateCalories } from '@/features/experience/calorie'
import PlanDraftView from '@/views/PlanDraftView.vue'
import ResultView from '@/views/ResultView.vue'

const databases: HachimiDatabase[] = []
const wrappers: VueWrapper[] = []
beforeEach(() => vi.stubGlobal('crypto', webcrypto))
afterEach(async () => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.unstubAllGlobals()
  for (const db of databases.splice(0)) { db.close(); await Dexie.delete(db.name) }
})

const setup = async (options: { fiveTips?: boolean; controlledClock?: boolean } = {}) => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const db = createHachimiDatabase(`synthetic-reminder-ui-${crypto.randomUUID()}`)
  databases.push(db)
  const draft = useDraftStore()
  await draft.load(createDexieDraftRepository(db))
  const candidate = { id: 'synthetic-confirmed', name: '深蹲', source_id: 'synthetic-source',
    segment: { start_seconds: 0, end_seconds: 20 },
    parameters: { mode: 'duration' as const, sets: 1, reps: null, duration_seconds: 15, rest_seconds: 0 },
    evidence: [], needs_confirmation: false,
    tips: [{ text: '起身时呼气', category: 'breathing' as const,
      evidence: { type: 'speech' as const, start_seconds: 5, end_seconds: 8 } },
    ...(options.fiveTips ? ['脚跟保持贴地', '双脚先站稳', '保持躯干平稳', '缓慢控制动作路径'].map((text, index) => ({
      text, category: 'path' as const,
      evidence: { type: 'speech' as const, start_seconds: 9 + index * 2, end_seconds: 10 + index * 2 },
    })) : [])],
  }
  draft.applyCandidateProposal([candidate, { ...candidate, id: 'synthetic-pending', name: '待核对动作', needs_confirmation: true }])
  await draft.flushPersist()
  const library = useLibraryStore()
  await library.load(createDexieLibraryRepository(db))
  const training = useTrainingStore()
  let elapsed = 0
  await training.load(createTrainingEngine({ persistence: createDexieTrainingRepository(db),
    clock: options.controlledClock ? { now: () => new Date(1_800_000_000_000 + elapsed), monotonicMilliseconds: () => elapsed } : undefined,
    estimateCalories: input => { const result = estimateCalories(input); return { value: result.kcal, method: result.method } } }))
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/plan', component: PlanDraftView }, { path: '/training', component: { template: '<div />' } },
    { path: '/train', component: { template: '<div />' } }, { path: '/personalize', component: { template: '<div />' } },
    { path: '/result/:recordId', component: ResultView },
  ] })
  await router.push('/plan')
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [pinia, router] } })
  wrappers.push(wrapper)
  return { db, draft, library, training, wrapper, router, advance: async (milliseconds: number) => {
    while (milliseconds > 0) {
      const step = Math.min(milliseconds, 1000)
      elapsed += step
      milliseconds -= step
      await training.tick()
    }
  } }
}

const button = (wrapper: VueWrapper, text: string) => wrapper.findAll('button').find(node => node.text() === text)!

const response = async (body: string) => {
  const input = JSON.parse(body)
  const action = input.actions[0]
  return new Response(JSON.stringify({ plan_id: input.plan_id, plan_revision: input.plan_revision,
    input_fingerprint: await fingerprintTrainingReminderInput(input), contract_version: 'training-reminders.v1.1',
    skill_version: '1.0.0', policy_version: 'ReminderPolicy.v1', mode: 'rules', fallback_reason: 'model_unavailable',
    reminders: [{ action_id: action.action_id, action_revision: action.action_revision, tip_id: action.tips[0].tip_id,
      stage: 'training', trigger: 'set_start', set_number: 1, active_seconds: null, priority: 1, max_plays: 1 }],
    budgets: [{ action_id: action.action_id, utterance_limit: 1, active_interval_seconds: 30, end_quiet_seconds: 10 }],
  }))
}

it('requires a separate tip review and speech opt-in, saves the arrangement, and starts only confirmed actions', async () => {
  const { db, draft, library, training, wrapper } = await setup()
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_, options) => response(options!.body as string))
  vi.stubGlobal('fetch', fetcher)
  const optIn = wrapper.get<HTMLInputElement>('input[aria-label="训练时听动作要点"]')
  expect(optIn.element.checked).toBe(false)
  await optIn.setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('先在动作中核对要点'))
  expect(fetcher).not.toHaveBeenCalled()
  await wrapper.findAll('.action-summary')[0]!.trigger('click')
  await wrapper.get('[data-full-editor]').trigger('click')
  expect(draft.items[0]!.reviewedSourceTips).toBeUndefined()
  await wrapper.get('input[aria-label="用于训练提醒：起身时呼气"]').setValue(true)
  await button(wrapper, '完成').trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  expect(fetcher).toHaveBeenCalledTimes(1)
  await library.reload()
  expect(library.preferences.speechReminders).toEqual({ automatic_speech: true, confirmed: true })
  const reopened = await createDexieLibraryRepository(db).openPlan(draft.plan.linkedPlanId!)
  expect(reopened.reminderCache?.plan.reminders).toHaveLength(1)
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session?.plan.items.map(item => item.name)).toEqual(['深蹲']))
  expect(training.session?.plan.reminderCache?.plan.reminders).toHaveLength(1)
})

it('shows a failed preference save without leaving an unsaved switch enabled', async () => {
  const { db, library, wrapper } = await setup()
  await library.load({ ...createDexieLibraryRepository(db), savePreferences: async () => { throw new Error('synthetic storage failure') } })
  const optIn = wrapper.get<HTMLInputElement>('input[aria-label="训练时听动作要点"]')
  await optIn.setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('开关没有保存成功'))
  expect(optIn.element.checked).toBe(false)
  expect(library.preferences.speechReminders).toBeUndefined()
})

it('starts with text only after the user disables a previously saved arrangement', async () => {
  const { draft, training, wrapper } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => response(options!.body as string)))
  const optIn = wrapper.get<HTMLInputElement>('input[aria-label="训练时听动作要点"]')
  await optIn.setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  await optIn.setValue(false)
  await vi.waitFor(() => expect(optIn.element.disabled).toBe(false))
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session).not.toBeNull())
  expect(training.session?.plan.reminderCache).toBeUndefined()
  expect(training.session?.plan.items[0]?.sourceTips?.[0]?.text).toBe('起身时呼气')
})

it('aborts an in-flight arrangement when turned off and ignores a late transport response', async () => {
  const { draft, wrapper } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  let finish!: (result: Response) => void
  let signal: AbortSignal | undefined
  let body = ''
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => {
    body = options!.body as string
    signal = options!.signal as AbortSignal
    return new Promise<Response>(resolve => { finish = resolve })
  }))
  const optIn = wrapper.get('input[aria-label="训练时听动作要点"]')
  await optIn.setValue(true)
  await vi.waitFor(() => expect(signal).toBeDefined())
  await optIn.setValue(false)
  await vi.waitFor(() => expect(signal?.aborted).toBe(true))
  finish(await response(body))
  await vi.waitFor(() => expect(wrapper.text()).not.toContain('正在安排提醒'))
  expect(draft.plan.reminderCache).toBeUndefined()
})

it('allows starting with text after one failed request without automatically retrying', async () => {
  const { draft, training, wrapper } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('private upstream detail', { status: 503 }))
  vi.stubGlobal('fetch', fetcher)
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒暂不可用'))
  expect(wrapper.text()).not.toContain('private upstream detail')
  await wrapper.findAll('.action-summary')[0]!.trigger('click')
  await wrapper.get('[data-full-editor]').trigger('click')
  await button(wrapper, '完成').trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒暂不可用'))
  expect(fetcher).toHaveBeenCalledTimes(1)
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session).not.toBeNull())
  expect(training.session?.plan.reminderCache).toBeUndefined()
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('does not report ready after a failed arrangement save and retries storage without another request', async () => {
  const { db, draft, wrapper } = await setup()
  let failCacheSave = true
  const repository = createDexieDraftRepository(db)
  await draft.load({ ...repository, save: async plan => {
    if (plan.reminderCache && failCacheSave) throw new Error('synthetic storage failure')
    return repository.save(plan)
  } })
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_, options) => response(options!.body as string))
  vi.stubGlobal('fetch', fetcher)
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒暂不可用'))
  expect(wrapper.text()).not.toContain('提醒安排已保存')
  failCacheSave = false
  await button(wrapper, '重试安排').trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  const reopened = await createDexieLibraryRepository(db).openPlan(draft.plan.linkedPlanId!)
  expect(reopened.reminderCache?.plan.reminders).toHaveLength(1)
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('waits for a changed speech preference to save before the start button becomes available', async () => {
  const { db, draft, library, training, wrapper } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => response(options!.body as string)))
  const optIn = wrapper.get('input[aria-label="训练时听动作要点"]')
  await optIn.setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  const repository = createDexieLibraryRepository(db)
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await library.load({ ...repository, savePreferences: async preference => { await gate; return repository.savePreferences(preference) } })
  await optIn.setValue(false)
  expect((button(wrapper, '开始训练').element as HTMLButtonElement).disabled).toBe(true)
  release()
  await vi.waitFor(() => expect((button(wrapper, '开始训练').element as HTMLButtonElement).disabled).toBe(false))
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session).not.toBeNull())
  expect(training.session?.plan.reminderCache).toBeUndefined()
})

it('does not revive speech when an unrelated coach preference is saved concurrently', async () => {
  const { db, library } = await setup()
  await library.setSpeechReminders(true)
  await Promise.all([library.setSpeechReminders(false), library.setPetVisible(false)])
  expect(library.preferences.speechReminders).toEqual({ automatic_speech: false, confirmed: true })
  const persisted = await createDexieLibraryRepository(db).loadPreferences()
  expect(persisted?.speechReminders).toEqual({ automatic_speech: false, confirmed: true })
  expect(persisted?.petVisible).toBe(false)
})

it('keeps speech off for this app even if the explicit off preference cannot be saved', async () => {
  const { db, draft, library, training, wrapper } = await setup()
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => response(options!.body as string)))
  const optIn = wrapper.get<HTMLInputElement>('input[aria-label="训练时听动作要点"]')
  await optIn.setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  await library.load({ ...createDexieLibraryRepository(db), savePreferences: async () => { throw new Error('synthetic storage failure') } })
  await optIn.setValue(false)
  await vi.waitFor(() => expect(wrapper.text()).toContain('开关没有保存成功'))
  expect(optIn.element.checked).toBe(false)
  await library.reload()
  expect(library.preferences.speechReminders).toEqual({ automatic_speech: false, confirmed: true })
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session).not.toBeNull())
  expect(training.session?.plan.reminderCache).toBeUndefined()
})

it('can start with text if only the reminder cache cannot be stored', async () => {
  const { db, draft, training, wrapper } = await setup()
  const repository = createDexieDraftRepository(db)
  await draft.load({ ...repository, save: async plan => {
    if (plan.reminderCache) throw new Error('synthetic cache write failure')
    return repository.save(plan)
  } })
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => response(options!.body as string)))
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒暂不可用'))
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session).not.toBeNull())
  expect(training.session?.plan.reminderCache).toBeUndefined()
  expect(training.session?.plan.items[0]?.sourceTips?.[0]?.text).toBe('起身时呼气')
})

it('does not leave the start button locked after the preference editor unmounts during a write', async () => {
  const { db, draft, library, wrapper } = await setup()
  const repository = createDexieLibraryRepository(db)
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await library.load({ ...repository, savePreferences: async preference => { await gate; return repository.savePreferences(preference) } })
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  expect((button(wrapper, '开始训练').element as HTMLButtonElement).disabled).toBe(true)
  draft.items.map(item => item.id).forEach(id => draft.remove(id))
  await vi.waitFor(() => expect(wrapper.find('input[aria-label="训练时听动作要点"]').exists()).toBe(false))
  release()
  await vi.waitFor(() => expect(library.preferencePending).toBe(false))
  draft.addManualAction({ name: '自建动作', mode: 'reps' })
  await vi.waitFor(() => expect((button(wrapper, '开始训练').element as HTMLButtonElement).disabled).toBe(false))
  await draft.flushPersist()
})

it('still refuses to start if the actual plan cannot save after omitting an optional failed arrangement', async () => {
  const { db, draft, training, wrapper } = await setup()
  const repository = createDexieDraftRepository(db)
  let failWrites = false
  await draft.load({ ...repository, save: async plan => {
    if (failWrites) throw new Error('synthetic global storage failure')
    return repository.save(plan)
  } })
  draft.reviewSourceTips(draft.items[0]!.id, [0])
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => {
    failWrites = true
    return response(options!.body as string)
  }))
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒暂不可用'))
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('这次没有开始训练'))
  expect(training.session).toBeNull()
  expect(draft.items[0]?.reps.value).toBeNull()
  expect(draft.items[0]?.durationSeconds.value).toBe(15)
})

it('replans a copied plan under its new identity while preserving all five reviewed source tips and the original archive', async () => {
  const { draft, library, wrapper } = await setup({ fiveTips: true })
  draft.reviewSourceTips(draft.items[0]!.id, [0, 1, 2, 3, 4])
  const sent: string[] = []
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_, options) => {
    sent.push(options!.body as string)
    return response(options!.body as string)
  })
  vi.stubGlobal('fetch', fetcher)
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  const originalId = draft.plan.linkedPlanId!
  const originalCache = JSON.parse(JSON.stringify(draft.plan.reminderCache))
  const tips = JSON.parse(JSON.stringify(draft.items[0]!.sourceTips))
  const reviews = JSON.parse(JSON.stringify(draft.items[0]!.reviewedSourceTips))
  await button(wrapper, '复制方案').trigger('click')
  await wrapper.get('input[aria-label="新方案名称"]').setValue('合成复练副本')
  await button(wrapper, '创建副本').trigger('click')
  await vi.waitFor(() => expect(draft.plan.linkedPlanId).not.toBe(originalId))
  const copyId = draft.plan.linkedPlanId!
  await vi.waitFor(() => expect(draft.plan.reminderCache?.plan.plan_id).toBe(copyId))
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  expect(sent.map(body => JSON.parse(body).plan_id)).toEqual([originalId, copyId])
  expect(draft.items[0]!.sourceTips).toEqual(tips)
  expect(draft.items[0]!.reviewedSourceTips).toEqual(reviews)
  await library.reload()
  expect(library.plans.find(plan => plan.id === originalId)?.reminderCache).toEqual(originalCache)
  const reopened = await library.openPlan(copyId)
  expect(reopened.items[0]!.sourceTips).toEqual(tips)
  expect(reopened.reminderCache?.plan.plan_id).toBe(copyId)
  await draft.reload()
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('replans a completed record under a new plan identity without changing its five tips or frozen history', async () => {
  const { draft, library, training, wrapper, router, advance } = await setup({ fiveTips: true, controlledClock: true })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  draft.reviewSourceTips(draft.items[0]!.id, [0, 1, 2, 3, 4])
  const sent: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, options) => {
    sent.push(options!.body as string)
    return response(options!.body as string)
  }))
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  const originalId = draft.plan.linkedPlanId!
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session?.plan.items).toHaveLength(1))
  expect((await training.prepareSet()).ok).toBe(true)
  await advance(3000)
  await advance(15000)
  expect(training.lastRecord?.outcome).toBe('completed')
  const record = JSON.parse(JSON.stringify(training.lastRecord!))
  expect(record.plan.items[0].sourceTips).toHaveLength(5)
  expect(record.plan.reminderCache.plan.plan_id).toBe(originalId)
  await library.refreshHistory()
  await router.push(`/result/${record.id}`)
  await button(wrapper, '再练一次').trigger('click')
  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/plan'))
  const nextId = draft.plan.linkedPlanId!
  expect(nextId).not.toBe(originalId)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  expect(sent.map(body => JSON.parse(body).plan_id)).toEqual([originalId, nextId])
  expect(draft.items[0]!.sourceTips).toEqual(record.plan.items[0].sourceTips)
  expect(draft.items[0]!.reviewedSourceTips).toEqual(record.plan.items[0].reviewedSourceTips)
  expect(draft.plan.reminderCache?.input.actions).toHaveLength(1)
  expect(draft.plan.reminderCache?.plan.plan_id).toBe(nextId)
  await library.refreshHistory()
  expect(library.records.find(entry => entry.id === record.id)).toEqual(record)
  const reopened = await library.openPlan(nextId)
  expect(reopened.reminderCache?.plan.plan_id).toBe(nextId)
  expect(reopened.items[0]!.sourceTips).toEqual(record.plan.items[0].sourceTips)
})

it('keeps a copied plan usable with all five reviewed tips after its new arrangement fails', async () => {
  const { draft, library, training, wrapper } = await setup({ fiveTips: true })
  draft.reviewSourceTips(draft.items[0]!.id, [0, 1, 2, 3, 4])
  const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(async (_, options) => response(options!.body as string))
    .mockResolvedValue(new Response('synthetic unavailable', { status: 503 }))
  vi.stubGlobal('fetch', fetcher)
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  const originalId = draft.plan.linkedPlanId!
  const originalCache = JSON.parse(JSON.stringify(draft.plan.reminderCache))
  const originalItem = JSON.parse(JSON.stringify(draft.items[0]))
  await button(wrapper, '复制方案').trigger('click')
  await wrapper.get('input[aria-label="新方案名称"]').setValue('合成文字跟练副本')
  await button(wrapper, '创建副本').trigger('click')
  await vi.waitFor(() => expect(draft.plan.linkedPlanId).not.toBe(originalId))
  const copyId = draft.plan.linkedPlanId!
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒暂不可用'))
  expect(wrapper.text()).not.toContain('提醒安排已保存')
  expect(draft.plan.reminderCache).toBeUndefined()
  expect(draft.items[0]).toEqual(originalItem)
  await library.reload()
  expect(library.plans.find(plan => plan.id === originalId)?.reminderCache).toEqual(originalCache)
  const reopened = await library.openPlan(copyId)
  expect(reopened.items[0]).toEqual(originalItem)
  expect(reopened.reminderCache).toBeUndefined()
  await button(wrapper, '开始训练').trigger('click')
  await vi.waitFor(() => expect(training.session).not.toBeNull())
  expect(training.session?.plan.reminderCache).toBeUndefined()
  expect(training.session?.plan.items[0]).toEqual(originalItem)
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('aborts the original arrangement while copying and cannot apply its late response to the new plan', async () => {
  const { draft, library, wrapper } = await setup({ fiveTips: true })
  draft.reviewSourceTips(draft.items[0]!.id, [0, 1, 2, 3, 4])
  let finishOriginal!: (result: Response) => void
  let originalSignal: AbortSignal | undefined
  const sent: string[] = []
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_, options) => {
    sent.push(options!.body as string)
    if (sent.length === 1) {
      originalSignal = options!.signal as AbortSignal
      return new Promise<Response>(resolve => { finishOriginal = resolve })
    }
    return response(options!.body as string)
  })
  vi.stubGlobal('fetch', fetcher)
  await wrapper.get('input[aria-label="训练时听动作要点"]').setValue(true)
  await vi.waitFor(() => expect(originalSignal).toBeDefined())
  const originalId = draft.plan.linkedPlanId!
  await button(wrapper, '复制方案').trigger('click')
  await wrapper.get('input[aria-label="新方案名称"]').setValue('合成迟到保护副本')
  await button(wrapper, '创建副本').trigger('click')
  await vi.waitFor(() => expect(draft.plan.linkedPlanId).not.toBe(originalId))
  const copyId = draft.plan.linkedPlanId!
  await vi.waitFor(() => expect(wrapper.text()).toContain('提醒安排已保存'))
  expect(originalSignal?.aborted).toBe(true)
  expect(sent.map(body => JSON.parse(body).plan_id)).toEqual([originalId, copyId])
  const copyCache = JSON.parse(JSON.stringify(draft.plan.reminderCache))
  const lateResponse = await response(sent[0]!)
  finishOriginal(lateResponse)
  await vi.waitFor(() => expect(lateResponse.bodyUsed).toBe(true))
  await draft.flushPersist()
  expect(draft.plan.reminderCache).toEqual(copyCache)
  expect(draft.plan.reminderCache?.plan.plan_id).toBe(copyId)
  await library.reload()
  expect(library.plans.find(plan => plan.id === originalId)?.reminderCache).toBeUndefined()
  const reopened = await library.openPlan(copyId)
  expect(reopened.reminderCache).toEqual(copyCache)
  expect(reopened.items[0]!.sourceTips).toHaveLength(5)
  expect(reopened.items[0]!.reviewedSourceTips).toHaveLength(5)
  expect(fetcher).toHaveBeenCalledTimes(2)
})
