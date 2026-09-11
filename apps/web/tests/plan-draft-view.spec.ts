import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan, DraftRepository } from '@/domain/types'
import type { TrainingSession } from '@/domain/training'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useLocalMediaStore } from '@/stores/local-media'
import { useTrainingStore } from '@/stores/training'
import type { TrainingCommand, TrainingEngine, TrainingEngineResult } from '@/training/training-engine'
import PlanDraftView from '@/views/PlanDraftView.vue'

class RecoverableDraftRepository implements DraftRepository {
  failSave = true
  value: DraftPlan | undefined

  async load(): Promise<DraftPlan | undefined> {
    return this.value
  }

  async save(plan: DraftPlan): Promise<void> {
    if (this.failSave) throw new Error('indexeddb unavailable')
    this.value = structuredClone(plan)
  }
}

const failingSaveAsRepository = (): LibraryRepository => ({
  listPlans: async () => [],
  listRecords: async () => [],
  loadProfile: async () => null,
  loadPreferences: async () => null,
  saveProfile: async () => { throw new Error('not used') },
  savePreferences: async () => { throw new Error('not used') },
  saveCurrentDraftAs: async () => { throw new Error('indexeddb transaction failed') },
  openPlan: async () => { throw new Error('not used') },
  deletePlan: async () => { throw new Error('not used') },
  replaceCurrentDraft: async () => { throw new Error('not used') },
  clearAllLocalData: async () => undefined,
})

const quickPlanRepository = (): LibraryRepository => ({
  listPlans: async () => [],
  listRecords: async () => [],
  loadProfile: async () => null,
  loadPreferences: async () => null,
  saveProfile: async () => { throw new Error('not used') },
  savePreferences: async () => { throw new Error('not used') },
  saveCurrentDraftAs: async () => { throw new Error('not used') },
  openPlan: async () => { throw new Error('not used') },
  deletePlan: async () => { throw new Error('not used') },
  replaceCurrentDraft: async ({ name, items }) => ({
    id: 'current',
    name,
    linkedPlanId: null,
    items: structuredClone(items),
    updatedAt: '2026-07-21T00:00:00.000Z',
  }),
  clearAllLocalData: async () => undefined,
})

class StartStorageFailureEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: null, record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    if (command.type !== 'session.create') throw new Error('unexpected command')
    return {
      ok: false,
      code: 'storage_unavailable',
      message: 'internal database details should not leak',
      session: null,
    }
  }
}

class InvalidPlanEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: null, record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    if (command.type !== 'session.create') throw new Error('unexpected command')
    return {
      ok: false,
      code: 'invalid_plan',
      message: '方案还不能开始训练',
      session: null,
      issues: [{
        itemId: command.plan.items[0]!.id,
        field: 'sets',
        message: '组数必须是正整数',
      }],
    }
  }
}

class ExistingSessionEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    const item = createQuickExperienceDraftItems()[0]!
    const session: TrainingSession = {
      id: 'current',
      sessionId: 'session-1',
      revision: 1,
      status: 'paused',
      pauseReason: 'recovered',
      plan: { name: '未完成训练', source: 'draft', sourcePlanId: null, items: [item] },
      currentItemIndex: 0,
      currentSetIndex: 0,
      currentSetActiveMilliseconds: 0,
      activeStartedAt: null,
      restStartedAt: null,
      restEndsAt: null,
      scheduledRestSeconds: null,
      creditedRestMilliseconds: 0,
      progress: [{ itemId: item.id, completedSets: 0, activeMilliseconds: 0, skipped: false }],
      petId: 'hachimi',
      coachStyleId: null,
      startedAt: '2026-07-21T00:00:00.000Z',
      updatedAt: '2026-07-21T00:01:00.000Z',
    }
    return { ok: true, session, record: null, events: [] }
  }

  async dispatch(_command: TrainingCommand): Promise<TrainingEngineResult> {
    throw new Error('not used')
  }
}

class CapturingStartEngine implements TrainingEngine {
  createdPlan: Extract<TrainingCommand, { type: 'session.create' }>['plan'] | null = null
  createdCoachStyleId: Extract<TrainingCommand, { type: 'session.create' }>['coachStyleId'] = null

  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: null, record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    if (command.type !== 'session.create') throw new Error('unexpected command')
    this.createdPlan = structuredClone(command.plan)
    this.createdCoachStyleId = command.coachStyleId
    return { ok: true, session: null, record: null, events: [] }
  }
}

describe('方案草稿保存状态', () => {
  afterEach(() => vi.useRealTimers())

  it('shows a retry action instead of claiming a failed automatic save succeeded', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    await vi.advanceTimersByTimeAsync(301)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('未保存，点击重试')
    repository.failSave = false
    await wrapper.get('button[aria-label="重试保存当前方案"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已自动保存到本机')
    expect(repository.value?.items[0]?.name).toBe('平板支撑')
  })

  it('keeps a continue-training entry when the current draft is empty', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    await useDraftStore().load(repository)
    await useTrainingStore().load(new ExistingSessionEngine())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plan', component: PlanDraftView },
        { path: '/training', component: { template: '<p>training</p>' } },
      ],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    expect(wrapper.find('.plan-card').exists()).toBe(false)
    expect(wrapper.get('.start-training-panel').text()).toContain('已有未完成训练')
    expect(wrapper.get('.start-training-panel a').text()).toBe('继续训练')
  })

  it('installs the labelled quick experience directly from an empty plan', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    await useLibraryStore().load(quickPlanRepository())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    await wrapper.get('.empty-plan button').trigger('click')
    await flushPromises()

    expect(draft.items).toHaveLength(3)
    expect(wrapper.text()).toContain('这个方案不是 AI 分析结果')
  })

  it('rejects an empty plan title without leaving a hidden stale value', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })
    const title = wrapper.get<HTMLInputElement>('input[aria-label="方案名称"]')

    await title.setValue('   ')

    expect(title.element.value).toBe('未命名方案')
    expect(draft.plan.name).toBe('未命名方案')
    expect(wrapper.get('.plan-name-error[role="alert"]').text()).toBe('方案名称不能为空')
  })

  it('keeps the save failure visible when deleting the final draft action fails', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    draft.remove(draft.items[0]!.id)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })
    await vi.advanceTimersByTimeAsync(301)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('未保存，点击重试')
    expect(wrapper.get('button[aria-label="重试保存当前方案"]')).toBeTruthy()
  })

  it('shows a safe retry when the save-as transaction fails after draft flush', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    await useLibraryStore().load(failingSaveAsRepository())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    await wrapper.get('section.save-as-panel > button').trigger('click')
    await wrapper.get('input[aria-label="新方案名称"]').setValue('我的训练')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.operation-error[role="alert"]').text()).toContain('复制方案没有成功')
    expect(wrapper.get('button[aria-label="重试复制方案"]')).toBeTruthy()
    expect(wrapper.text()).not.toContain('indexeddb transaction failed')
  })

  it('shows a safe retry when starting training cannot write local data', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    await useTrainingStore().load(new StartStorageFailureEngine())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    expect(wrapper.get('.training-safety-tip').text()).toBe('如有不适请停止，并按自身情况调整')
    await wrapper.get('.start-training-panel button').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/plan')
    expect(wrapper.get('.operation-error[role="alert"]').text()).toContain('没有开始训练')
    expect(wrapper.get('button[aria-label="重试开始训练"]')).toBeTruthy()
    expect(wrapper.text()).not.toContain('internal database details should not leak')
  })

  it('shows a plan validation issue beside the affected action', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    await useTrainingStore().load(new InvalidPlanEngine())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] },
    })

    await wrapper.get('.start-training-panel button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.plan-card')
    expect(card.classes()).toContain('has-validation-error')
    expect(card.get('.card-validation[role="alert"]').text()).toContain('组数必须是正整数')
    expect(document.activeElement).toBe(card.element)
  })

  it('marks uncertain actions, confirms them in the sheet, and restores focus', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.applyCandidateProposal([{
      id: 'candidate-pending',
      name: '疑似弯举',
      source_id: 'video-a',
      segment: { start_seconds: 4, end_seconds: 12 },
      parameters: {
        mode: null,
        sets: null,
        reps: null,
        duration_seconds: null,
        rest_seconds: null,
      },
      evidence: [{ type: 'visual', start_seconds: 4, end_seconds: 12 }],
      needs_confirmation: true,
    }], {}, 'replace')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] },
    })

    const trigger = wrapper.get<HTMLButtonElement>('.action-summary')
    expect(wrapper.get('.pending-badge').text()).toBe('待确认')
    expect(wrapper.get('.start-training-panel button').attributes('disabled')).toBeDefined()
    await trigger.trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(wrapper.get('.action-sheet .plan-name').element)

    await wrapper.get('.action-sheet').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.find('.action-sheet').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)

    await trigger.trigger('click')
    await flushPromises()
    await wrapper.get('.action-sheet .done').trigger('click')
    await flushPromises()
    expect(draft.items[0]!.confirmationStatus).toBe('confirmed')
    expect(wrapper.find('.pending-badge').exists()).toBe(false)
  })

  it('starts training with only confirmed actions', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '已确认动作', mode: 'reps' })
    draft.applyCandidateProposal([{
      id: 'candidate-pending',
      name: '待确认动作',
      source_id: 'video-a',
      segment: { start_seconds: 4, end_seconds: 12 },
      parameters: {
        mode: 'reps',
        sets: 3,
        reps: 10,
        duration_seconds: null,
        rest_seconds: 60,
      },
      evidence: [{ type: 'visual', start_seconds: 4, end_seconds: 12 }],
      needs_confirmation: true,
    }])
    const engine = new CapturingStartEngine()
    await useTrainingStore().load(engine)
    useLibraryStore().preferences.coachStyleId = 'zen'
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plan', component: PlanDraftView },
        { path: '/training', component: { template: '<p>training</p>' } },
      ],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    await wrapper.get('.start-training-panel button').trigger('click')
    await flushPromises()

    expect(engine.createdPlan?.items.map((item) => item.name)).toEqual(['已确认动作'])
    expect(engine.createdCoachStyleId).toBe('zen')
    expect(router.currentRoute.value.path).toBe('/training')
  })

  it('shows a safe original-video link only for an action with a source URL', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.applyCandidateProposal([{
      id: 'candidate-a',
      name: '拖拽弯举',
      source_id: 'video-a',
      segment: { start_seconds: 41, end_seconds: 51 },
      parameters: {
        mode: 'reps',
        sets: 3,
        reps: 10,
        duration_seconds: null,
        rest_seconds: 60,
      },
      evidence: [{ type: 'visual', start_seconds: 41, end_seconds: 51 }],
      needs_confirmation: false,
    }], {
      'video-a': {
        title: '来源视频 A',
        origin_url: 'https://www.douyin.com/video/123456',
      },
    })
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    const links = wrapper.findAll('a.original-video-link')
    expect(links).toHaveLength(1)
    expect(links[0]!.text()).toBe('查看原视频')
    expect(links[0]!.attributes()).toMatchObject({
      href: 'https://www.douyin.com/video/123456',
      target: '_blank',
      rel: 'noopener noreferrer',
    })
  })

  it('restores a local source preview from the immutable draft snapshot', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    const sourceId = 'local:plan-preview'
    draft.applyCandidateProposal([{
      id: 'candidate-local',
      name: '本地弯举',
      source_id: sourceId,
      segment: { start_seconds: 4, end_seconds: 12 },
      parameters: {
        mode: 'reps',
        sets: 3,
        reps: 10,
        duration_seconds: null,
        rest_seconds: 60,
      },
      evidence: [{ type: 'visual', start_seconds: 4, end_seconds: 12 }],
      needs_confirmation: false,
    }], {
      [sourceId]: {
        kind: 'local',
        title: 'local.mp4',
        origin_url: null,
        localMedia: {
          fileName: 'local.mp4',
          mimeType: 'video/mp4',
          sizeBytes: 12,
          lastModified: 1,
          durationSeconds: 30,
        },
      },
    })
    vi.spyOn(useLocalMediaStore(), 'resolve').mockResolvedValue('blob:plan-preview')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plan', component: PlanDraftView }],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })

    expect(wrapper.text()).not.toContain('跟练执行')
    expect(wrapper.text()).not.toContain('教学演示')
    await wrapper.get('button.local-preview-button').trigger('click')
    await flushPromises()

    expect(wrapper.get('video').attributes('src')).toBe('blob:plan-preview')
  })

  it('previews, applies, and restores a deterministic TrainPal adjustment', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new RecoverableDraftRepository()
    repository.failSave = false
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '深蹲', mode: 'reps' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/plan', component: PlanDraftView },
        { path: '/personalize', component: { template: '<p>GYMTI</p>' } },
      ],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(PlanDraftView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] },
    })

    await wrapper.get('button[data-adjustment-trigger]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('调整这次训练')
    await wrapper.get('button[data-adjustment-intent="more_challenging"]').trigger('click')
    await wrapper.get('button[data-generate-adjustment]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.adjustment-change-list').text()).toContain('10 次 → 12 次')
    await wrapper.get('button[data-adjustment-intent="easier_to_finish"]').trigger('click')
    expect(wrapper.find('button[data-apply-adjustment]').exists()).toBe(false)
    expect(wrapper.find('.adjustment-change-list').exists()).toBe(false)
    await wrapper.get('button[data-adjustment-intent="more_challenging"]').trigger('click')
    await wrapper.get('button[data-generate-adjustment]').trigger('click')
    await wrapper.get('button[data-adjustment-safety-stop]').trigger('click')
    expect(wrapper.find('button[data-apply-adjustment]').exists()).toBe(false)
    expect(draft.adjustmentProposal).toBeNull()
    await wrapper.get('button[data-adjustment-safety-stop]').trigger('click')
    await wrapper.get('button[data-generate-adjustment]').trigger('click')
    await flushPromises()
    await wrapper.get('button[data-apply-adjustment]').trigger('click')
    await flushPromises()
    expect(draft.items[0]!.reps).toEqual({ value: 12, source: 'personalized' })
    expect(wrapper.text()).toContain('已为本次训练调整')

    await wrapper.get('button[data-restore-base-plan]').trigger('click')
    await flushPromises()
    expect(draft.items[0]!.reps).toEqual({ value: 10, source: 'rule' })

    await wrapper.get('button[data-adjustment-trigger]').trigger('click')
    await wrapper.get('button[data-adjustment-safety-stop]').trigger('click')
    await wrapper.get('button[data-adjustment-intent="more_challenging"]').trigger('click')
    await wrapper.get('button[data-generate-adjustment]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('有不适时不会生成调整')
    expect(wrapper.find('.adjustment-change-list').exists()).toBe(false)
  })
})
