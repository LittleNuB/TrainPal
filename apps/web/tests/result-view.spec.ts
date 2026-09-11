import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan } from '@/domain/types'
import type { TrainingRecord, TrainingSession } from '@/domain/training'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'
import ResultView from '@/views/ResultView.vue'

const posterMocks = vi.hoisted(() => ({
  deliver: vi.fn(async () => 'shared' as const),
  download: vi.fn(),
  render: vi.fn(async () => new Blob(['poster'], { type: 'image/png' })),
}))

vi.mock('@/features/experience/poster', () => ({
  deliverCompletionPoster: posterMocks.deliver,
  downloadCompletionPoster: posterMocks.download,
  renderCompletionPoster: posterMocks.render,
}))

const makeRecord = (): TrainingRecord => ({
  id: 'record-1',
  outcome: 'completed',
  plan: {
    name: '历史方案',
    source: 'saved',
    sourcePlanId: 'plan-1',
    items: createQuickExperienceDraftItems(),
  },
  actions: [],
  activeSeconds: 60,
  creditedRestSeconds: 0,
  trainingDurationSeconds: 60,
  completedActionCount: 1,
  calorie: { value: 4, method: 'generic' },
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  endedAt: '2026-07-21T00:01:00.000Z',
})

const makeSession = (record: TrainingRecord): TrainingSession => ({
  id: 'current',
  sessionId: 'session-current',
  revision: 1,
  status: 'paused',
  pauseReason: 'user',
  plan: record.plan,
  currentItemIndex: 0,
  currentSetIndex: 0,
  currentSetActiveMilliseconds: 0,
  activeStartedAt: null,
  restStartedAt: null,
  restEndsAt: null,
  scheduledRestSeconds: null,
  creditedRestMilliseconds: 0,
  progress: record.plan.items.map((item) => ({
    itemId: item.id,
    completedSets: 0,
    activeMilliseconds: 0,
    skipped: false,
  })),
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  updatedAt: '2026-07-21T00:01:00.000Z',
})

const setup = async (options: { failReplacement?: boolean; failSave?: boolean } = {}) => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const record = makeRecord()
  const replacementCalls: Array<Pick<DraftPlan, 'name' | 'items'>> = []
  const savedDrafts: DraftPlan[] = []
  const repository: LibraryRepository = {
    listPlans: async () => [],
    listRecords: async () => [record],
    loadProfile: async () => null,
    loadPreferences: async () => null,
    saveProfile: async () => { throw new Error('not used') },
    savePreferences: async () => { throw new Error('not used') },
    saveCurrentDraftAs: async () => { throw new Error('not used') },
    openPlan: async () => { throw new Error('not used') },
    deletePlan: async () => { throw new Error('not used') },
    replaceCurrentDraft: async (input) => {
      replacementCalls.push(JSON.parse(JSON.stringify(input)) as Pick<DraftPlan, 'name' | 'items'>)
      if (options.failReplacement) throw new Error('indexeddb details must not leak')
      return {
        id: 'current',
        linkedPlanId: null,
        name: input.name,
        items: structuredClone(input.items),
        updatedAt: '2026-07-21T00:02:00.000Z',
      }
    },
    clearAllLocalData: async () => undefined,
  }
  await useLibraryStore().load(repository)
  await useDraftStore().load({
    load: async () => undefined,
    save: async (plan) => {
      if (options.failSave) throw new Error('storage details must not leak')
      savedDrafts.push(structuredClone(plan))
    },
  })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<p>home</p>' } },
      { path: '/mine', component: { template: '<p>mine</p>' } },
      { path: '/plan', component: { template: '<p>plan</p>' } },
      { path: '/training', component: { template: '<p>training</p>' } },
      { path: '/result/:recordId', component: ResultView },
    ],
  })
  await router.push('/result/record-1')
  await router.isReady()
  const wrapper = mount(ResultView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { record, replacementCalls, savedDrafts, router, wrapper }
}

describe('训练结果', () => {
  afterEach(() => vi.restoreAllMocks())

  it('presents one warm result summary before progressively disclosing action details', async () => {
    const context = await setup()

    expect(context.wrapper.find('.trainpal-coach').exists()).toBe(false)
    expect(context.wrapper.get('.coach-result').text()).toContain('TrainPal 留言')
    expect(context.wrapper.get('.result-metrics').text()).toContain('约 4')
    expect(context.wrapper.get('.action-results').attributes('open')).toBeUndefined()
    expect(context.wrapper.findAll('footer button')).toHaveLength(1)
  })

  it('announces poster generation while the asynchronous share is pending', async () => {
    let completeShare: ((result: 'shared') => void) | undefined
    posterMocks.deliver.mockImplementationOnce(
      () => new Promise((resolve) => { completeShare = resolve }),
    )
    const context = await setup()

    await context.wrapper.get('.poster-actions button').trigger('click')

    expect(context.wrapper.get('[role="status"]').text()).toContain('正在生成海报')
    expect(context.wrapper.get('.poster-actions button').attributes('disabled')).toBeDefined()

    completeShare?.('shared')
    await flushPromises()
    expect(context.wrapper.get('[role="status"]').text()).toContain('已打开分享')
  })

  it('renders the completed motion from the immutable record style snapshot', async () => {
    const context = await setup()
    context.record.coachStyleId = 'gentle'
    useLibraryStore().records = [structuredClone(context.record)]
    await flushPromises()

    const coach = context.wrapper.get('.coach-motion')
    expect(coach.attributes('data-style')).toBe('gentle')
    expect(coach.attributes('data-action')).toBe('comfort')
  })

  it('keeps the TrainPal message when the user hides the cat coach', async () => {
    const context = await setup()
    useLibraryStore().preferences.petVisible = false
    await flushPromises()

    expect(context.wrapper.find('.trainpal-coach').exists()).toBe(false)
    expect(context.wrapper.get('.coach-result').text()).toContain('TrainPal 留言')
  })

  it('does not overwrite a different non-empty draft when the user cancels', async () => {
    const context = await setup()
    const draft = useDraftStore()
    draft.addManualAction({ name: '当前草稿动作', mode: 'reps' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    await context.wrapper.get('footer button').trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith('再练一次会替换当前方案，确定继续吗？')
    expect(context.replacementCalls).toEqual([])
    expect(draft.items[0]?.name).toBe('当前草稿动作')
    expect(context.router.currentRoute.value.path).toBe('/result/record-1')
  })

  it('restores the immutable snapshot after replacement is confirmed', async () => {
    const context = await setup()
    const draft = useDraftStore()
    draft.addManualAction({ name: '当前草稿动作', mode: 'duration' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await context.wrapper.get('footer button').trigger('click')
    await flushPromises()

    expect(context.replacementCalls).toHaveLength(1)
    expect(draft.plan.name).toBe(context.record.plan.name)
    expect(draft.items).toEqual(context.record.plan.items)
    expect(context.router.currentRoute.value.path).toBe('/plan')
  })

  it('saves pending edits before restoring a past training plan', async () => {
    const context = await setup()
    useDraftStore().addManualAction({ name: '必须保留的新动作', mode: 'reps' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await context.wrapper.get('footer button').trigger('click')
    await flushPromises()

    expect(context.savedDrafts.at(-1)?.items[0]?.name).toBe('必须保留的新动作')
    expect(context.replacementCalls).toHaveLength(1)
  })

  it('does not replace the current plan when saving pending edits fails', async () => {
    const context = await setup({ failSave: true })
    useDraftStore().addManualAction({ name: '必须保留的新动作', mode: 'reps' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await context.wrapper.get('footer button').trigger('click')
    await flushPromises()

    expect(context.replacementCalls).toHaveLength(0)
    expect(useDraftStore().items[0]?.name).toBe('必须保留的新动作')
    expect(context.router.currentRoute.value.path).toBe('/result/record-1')
    expect(context.wrapper.get('[role="status"]').text()).toContain('没有恢复成功')
    expect(context.wrapper.text()).not.toContain('storage details')
  })

  it('keeps the user on the result and hides storage details when replacement fails', async () => {
    const context = await setup({ failReplacement: true })

    await context.wrapper.get('footer button').trigger('click')
    await flushPromises()

    expect(context.router.currentRoute.value.path).toBe('/result/record-1')
    expect(context.wrapper.get('[role="status"]').text()).toContain('没有恢复成功')
    expect(context.wrapper.text()).not.toContain('indexeddb details')
  })

  it('keeps an equivalent saved-plan link instead of rewriting the current draft', async () => {
    const context = await setup()
    const draft = useDraftStore()
    draft.adoptPersistedPlan({
      id: 'current',
      name: context.record.plan.name,
      linkedPlanId: 'plan-1',
      items: structuredClone(context.record.plan.items),
      updatedAt: '2026-07-21T00:02:00.000Z',
    })

    await context.wrapper.get('footer button').trigger('click')
    await flushPromises()

    expect(context.replacementCalls).toEqual([])
    expect(draft.plan.linkedPlanId).toBe('plan-1')
    expect(context.router.currentRoute.value.path).toBe('/plan')
  })

  it('continues an unfinished session without replacing the draft', async () => {
    const context = await setup()
    useTrainingStore().session = makeSession(context.record)
    await flushPromises()

    expect(context.wrapper.get('footer button').text()).toBe('继续当前训练')
    await context.wrapper.get('footer button').trigger('click')
    await flushPromises()

    expect(context.replacementCalls).toEqual([])
    expect(context.router.currentRoute.value.path).toBe('/training')
  })
})
