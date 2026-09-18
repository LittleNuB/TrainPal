import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type { AnalysisCandidate, DraftPlan, DraftRepository } from '@/domain/types'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import VideoAnalysisView from '@/views/VideoAnalysisView.vue'

class MemoryDraftRepository implements DraftRepository {
  value: DraftPlan | undefined
  saveCount = 0
  failAt = 0

  async load(): Promise<DraftPlan | undefined> {
    return structuredClone(this.value)
  }

  async save(plan: DraftPlan): Promise<void> {
    this.saveCount += 1
    if (this.failAt === this.saveCount) throw new Error('indexeddb write failed')
    this.value = structuredClone(plan)
  }
}

const reliableCandidate = (input: Partial<AnalysisCandidate> = {}): AnalysisCandidate => ({
  id: 'candidate-reliable',
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
  ...input,
})

const pendingCandidate = (): AnalysisCandidate => reliableCandidate({
  id: 'candidate-pending',
  name: '疑似手臂动作',
  segment: { start_seconds: 12, end_seconds: 18 },
  parameters: {
    mode: null,
    sets: null,
    reps: null,
    duration_seconds: null,
    rest_seconds: null,
  },
  needs_confirmation: true,
})

const prepareControlledAnalysis = (
  analysis: ReturnType<typeof useAnalysisStore>,
  status: 'running' | 'completed' | 'failed' | 'cancelled' = 'completed',
): void => {
  analysis.currentSourceId = 'video-a'
  analysis.currentSourceKind = 'controlled'
  analysis.sources = [{
    id: 'video-a',
    title: '来源视频 A',
    media_url: '/api/v1/sources/video-a/media',
    duration_seconds: 60,
    origin_url: null,
  }]
  analysis.status = status
  analysis.stage = status === 'running' ? 'analyzing_evidence' : status
  analysis.sourceDurationSeconds = 60
  analysis.processedSeconds = status === 'running' ? 24 : 60
  analysis.discoveredCandidateCount = status === 'running' ? 2 : 0
  analysis.coverageStatus = status === 'completed' ? 'complete' : null
}

const mountView = async (input?: {
  attachTo?: HTMLElement
}): Promise<{
  wrapper: VueWrapper
  router: Router
  analysis: ReturnType<typeof useAnalysisStore>
}> => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const analysis = useAnalysisStore()
  prepareControlledAnalysis(analysis)
  vi.spyOn(analysis, 'loadSources').mockResolvedValue()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<p>home</p>' } },
      { path: '/analysis', component: VideoAnalysisView },
      { path: '/plan', component: { template: '<p>plan</p>' } },
      { path: '/train', component: { template: '<p>train</p>' } },
    ],
  })
  await router.push('/analysis')
  await router.isReady()
  const wrapper = mount(VideoAnalysisView, {
    attachTo: input?.attachTo,
    global: { plugins: [pinia, router] },
  })
  await flushPromises()
  return { wrapper, router, analysis }
}

describe('视频动作分析页', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows only the current source, real progress, and cancellation while running', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    prepareControlledAnalysis(analysis, 'running')
    const cancel = vi.spyOn(analysis, 'cancel').mockResolvedValue()
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<p>home</p>' } },
        { path: '/analysis', component: VideoAnalysisView },
        { path: '/plan', component: { template: '<p>plan</p>' } },
      ],
    })
    await router.push('/analysis')
    await router.isReady()
    const wrapper = mount(VideoAnalysisView, { global: { plugins: [pinia, router] } })
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('来源视频 A')
    expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('40')
    expect(wrapper.get('.discovery-count strong').text()).toBe('2')
    expect(wrapper.get('.discovery-count span').text()).toBe('个动作线索')
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.find('.candidate-panel').exists()).toBe(false)

    await wrapper.get('.cancel-action').trigger('click')
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('compiles reliable and pending candidates into an empty current plan', async () => {
    const repository = new MemoryDraftRepository()
    const { wrapper, router, analysis } = await mountView()
    const draft = useDraftStore()
    await draft.load(repository)
    prepareControlledAnalysis(analysis)
    analysis.candidates = [reliableCandidate(), pendingCandidate()]
    await flushPromises()

    expect(wrapper.get('#completion-title').text()).toBe('训练方案已准备好')
    expect(wrapper.text()).toContain('1 个会在方案中标记为待确认')
    await wrapper.get('.prepared-plan-action').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/plan')
    expect(draft.items).toHaveLength(2)
    expect(draft.items.map((item) => item.confirmationStatus)).toEqual(['pending', 'confirmed'])
    expect(draft.items[0]).toMatchObject({ mode: 'reps', reps: { value: 10, source: 'rule' } })
    expect(repository.value?.items.every((item) => !('segmentRole' in item))).toBe(true)
  })

  it('asks whether to append or replace and does not mutate before that choice', async () => {
    const repository = new MemoryDraftRepository()
    const { wrapper, router, analysis } = await mountView({ attachTo: document.body })
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '已有动作', mode: 'duration' })
    await draft.flushPersist()
    prepareControlledAnalysis(analysis)
    analysis.candidates = [reliableCandidate()]
    await flushPromises()

    const trigger = wrapper.get<HTMLButtonElement>('.prepared-plan-action')
    await trigger.trigger('click')
    await flushPromises()
    expect(draft.items.map((item) => item.name)).toEqual(['已有动作'])
    expect(document.activeElement).toBe(wrapper.get('.append-option').element)
    expect(wrapper.get('.proposal-dialog').text()).toContain('要加入当前方案，还是替换它')

    await wrapper.get('.append-option').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/plan')
    expect(draft.items.map((item) => item.name)).toEqual(['已有动作', '拖拽弯举'])
  })

  it('supports cancel and replace as explicit alternatives', async () => {
    const repository = new MemoryDraftRepository()
    const { wrapper, router, analysis } = await mountView({ attachTo: document.body })
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '已有动作', mode: 'duration' })
    await draft.flushPersist()
    prepareControlledAnalysis(analysis)
    analysis.candidates = [reliableCandidate()]
    await flushPromises()

    const trigger = wrapper.get<HTMLButtonElement>('.prepared-plan-action')
    await trigger.trigger('click')
    await flushPromises()
    await wrapper.get('.proposal-dialog').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.find('.proposal-dialog').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    expect(draft.items.map((item) => item.name)).toEqual(['已有动作'])

    await trigger.trigger('click')
    await flushPromises()
    await wrapper.get('.proposal-options button:nth-child(2)').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/plan')
    expect(draft.items.map((item) => item.name)).toEqual(['拖拽弯举'])
  })

  it('rolls back a failed proposal write and exposes a retryable error', async () => {
    const repository = new MemoryDraftRepository()
    repository.failAt = 2
    const { wrapper, router, analysis } = await mountView()
    const draft = useDraftStore()
    await draft.load(repository)
    prepareControlledAnalysis(analysis)
    analysis.candidates = [reliableCandidate()]
    await flushPromises()

    await wrapper.get('.prepared-plan-action').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/analysis')
    expect(draft.items).toEqual([])
    expect(wrapper.get('.inline-error[role="alert"]').text()).toContain('原方案保持不变')
    expect(wrapper.get('.prepared-plan-action').attributes('disabled')).toBeUndefined()
  })

  it('keeps coverage gaps outside the proposal and lets the user retry one', async () => {
    const { wrapper, analysis } = await mountView()
    prepareControlledAnalysis(analysis)
    analysis.candidates = [reliableCandidate()]
    analysis.coverageStatus = 'partial'
    analysis.coverageGaps = [{
      start_seconds: 20,
      end_seconds: 30,
      reason: 'timeout',
      retryable: true,
    }]
    vi.spyOn(analysis, 'retryGap').mockResolvedValue()
    await flushPromises()

    expect(wrapper.get('.coverage-gaps').text()).toContain('0:20—0:30')
    expect(wrapper.get('.completion-metrics').text()).toContain('覆盖缺口')
    expect(wrapper.get('.coverage-gaps button').attributes('disabled')).toBeUndefined()

    analysis.coverageStatus = 'insufficient'
    await flushPromises()
    expect(wrapper.text()).toContain('可靠覆盖还不够生成方案')
    expect(wrapper.text()).toContain('补齐可靠证据后')
    expect(wrapper.find('.prepared-plan-action').exists()).toBe(false)
    expect(wrapper.get('.coverage-gaps button').text()).toBe('重试这段')
    expect(wrapper.get('.state-actions a[href="/plan"]').text()).toBe('手工创建动作')
  })

  it('separates empty evidence, system failure, and busy capacity', async () => {
    const { wrapper, analysis } = await mountView()
    prepareControlledAnalysis(analysis)
    analysis.candidates = []
    await flushPromises()
    expect(wrapper.text()).toContain('没有足够可靠的动作证据')
    expect(wrapper.text()).not.toContain('这次没有分析成功')

    analysis.status = 'failed'
    analysis.failureKind = 'system'
    analysis.error = {
      code: 'provider_error',
      message: '动作分析暂时不可用，请重试',
      retryable: true,
    }
    await flushPromises()
    expect(wrapper.text()).toContain('这次没有分析成功')

    analysis.failureKind = 'capacity'
    analysis.retryAfterSeconds = 12
    await flushPromises()
    expect(wrapper.text()).toContain('实时 AI 名额正在使用')
    expect(wrapper.text()).toContain('约 12 秒后可重试')
  })

  it('does not cancel or disconnect the Analysis Run when the page leaves', async () => {
    const { wrapper, analysis } = await mountView()
    prepareControlledAnalysis(analysis, 'running')
    const cancel = vi.spyOn(analysis, 'cancel').mockResolvedValue()
    const disconnect = vi.spyOn(analysis, 'disconnect')

    wrapper.unmount()

    expect(cancel).not.toHaveBeenCalled()
    expect(disconnect).not.toHaveBeenCalled()
  })
})
