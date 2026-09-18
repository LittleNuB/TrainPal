import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { SourceSummary } from '@/domain/types'
import type { CoachStyleId } from '@/domain/coach'
import type { TrainingRecord, TrainingSession } from '@/domain/training'
import CoachMotion from '@/features/experience/CoachMotion.vue'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { useAnalysisStore } from '@/stores/analysis'
import { useLocalMediaStore } from '@/stores/local-media'
import { useTrainingStore } from '@/stores/training'
import type { TrainingCommand, TrainingEngine, TrainingEngineResult } from '@/training/training-engine'
import TrainingView from '@/views/TrainingView.vue'

const trainingSession = (
  status: TrainingSession['status'],
  currentItemIndex = 0,
  coachStyleId: CoachStyleId | null = null,
): TrainingSession => {
  const baseItems = createQuickExperienceDraftItems()
  const items = [
    baseItems[0]!,
    baseItems[1]!,
    { ...structuredClone(baseItems[0]!), id: 'quick-action-3', name: '第三个动作' },
  ]
  return {
    id: 'current',
    sessionId: 'session-1',
    revision: 1,
    status,
    pauseReason: status === 'paused' ? 'user' : null,
    plan: { name: '三动作训练', source: 'sample', sourcePlanId: null, items },
    currentItemIndex,
    currentSetIndex: 0,
    currentSetActiveMilliseconds: 0,
    activeStartedAt: status === 'active' ? '2026-07-21T00:00:00.000Z' : null,
    restStartedAt: null,
    restEndsAt: null,
    scheduledRestSeconds: null,
    creditedRestMilliseconds: 0,
    progress: items.map((item) => ({
      itemId: item.id,
      completedSets: 0,
      activeMilliseconds: 0,
      skipped: false,
    })),
    petId: 'hachimi',
    coachStyleId,
    startedAt: '2026-07-21T00:00:00.000Z',
    updatedAt: '2026-07-21T00:00:00.000Z',
  }
}

class CueEngine implements TrainingEngine {
  constructor(
    private current: TrainingSession,
    private readonly options: { fail?: boolean; terminal?: boolean } = {},
  ) {}

  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: this.current, record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    if (this.options.fail) {
      return {
        ok: false,
        code: 'invalid_transition',
        message: '当前操作失败',
        session: this.current,
      }
    }

    const item = this.current.plan.items[this.current.currentItemIndex]!
    if (command.type === 'set.start') {
      this.current = { ...this.current, status: 'active', revision: this.current.revision + 1 }
      return {
        ok: true,
        session: this.current,
        record: null,
        events: [{ type: 'set.started', itemId: item.id, setIndex: this.current.currentSetIndex }],
      }
    }

    if (command.type === 'set.complete' && this.options.terminal) {
      const record: TrainingRecord = {
        id: this.current.sessionId,
        outcome: 'completed',
        plan: this.current.plan,
        actions: [],
        activeSeconds: 1,
        creditedRestSeconds: 0,
        trainingDurationSeconds: 1,
        completedActionCount: 1,
        calorie: { value: 4, method: 'generic' },
        petId: 'hachimi',
        coachStyleId: this.current.coachStyleId,
        startedAt: this.current.startedAt,
        endedAt: '2026-07-21T00:01:00.000Z',
      }
      return {
        ok: true,
        session: null,
        record,
        events: [
          { type: 'set.completed', itemId: item.id, setIndex: this.current.currentSetIndex },
          { type: 'session.completed', recordId: record.id },
        ],
      }
    }

    if (command.type === 'set.complete') {
      this.current = {
        ...this.current,
        status: 'resting',
        revision: this.current.revision + 1,
        restStartedAt: new Date().toISOString(),
        restEndsAt: new Date(Date.now() + 30_000).toISOString(),
        scheduledRestSeconds: 30,
      }
      return {
        ok: true,
        session: this.current,
        record: null,
        events: [{ type: 'set.completed', itemId: item.id, setIndex: 0 }],
      }
    }

    return { ok: true, session: this.current, record: null, events: [] }
  }
}

class SessionEngine implements TrainingEngine {
  commands: TrainingCommand[] = []

  constructor(
    private current: TrainingSession,
    private readonly conflict = false,
  ) {}

  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: this.current, record: null, events: [] }
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    this.commands.push(command)
    if (this.conflict) {
      return {
        ok: false,
        code: 'session_conflict',
        message: '训练状态已在其他页面更新，请继续最新进度',
        session: { ...this.current, revision: this.current.revision + 1 },
      }
    }
    this.current = { ...this.current, status: 'active', revision: this.current.revision + 1 }
    return { ok: true, session: this.current, record: null, events: [] }
  }
}

const source: SourceSummary = {
  id: 'controlled-video',
  title: '受控视频',
  media_url: '/api/v1/sources/controlled-video/media',
  duration_seconds: 60,
  origin_url: null,
}

const mountTraining = async (pinia: ReturnType<typeof createPinia>) => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/plan', component: { template: '<p>plan</p>' } },
      { path: '/training', component: TrainingView },
    ],
  })
  await router.push('/training')
  await router.isReady()
  const wrapper = mount(TrainingView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

describe('训练页合同', () => {
  it.each(['hidden', 'pagehide', 'route'])('pauses a late preparation commit after %s', async (leave) => {
    const pinia = createPinia()
    setActivePinia(pinia)
    let current = trainingSession('paused')
    current.flowVersion = 'watch-v1'
    current.pauseReason = 'before_start'
    let release!: () => void
    const write = new Promise<void>((resolve) => { release = resolve })
    const commands: string[] = []
    const engine: TrainingEngine = {
      restore: async () => ({ ok: true, session: current, record: null, events: [] }),
      dispatch: async (command) => {
        commands.push(command.type)
        if (command.type === 'set.prepare') {
          await write
          current = { ...current, status: 'countdown', revision: current.revision + 1 }
        } else if (command.type === 'session.pause') {
          current = { ...current, status: 'paused', pauseReason: 'page_hidden', revision: current.revision + 1 }
        }
        return { ok: true, session: current, record: null, events: [] }
      },
    }
    const training = useTrainingStore()
    await training.load(engine)
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/training', component: TrainingView },
      { path: '/plan', component: { template: '<p>plan</p>' } },
    ] })
    await router.push('/training')
    const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [pinia, router] } })
    await flushPromises()
    await wrapper.get('button.primary-action').trigger('click')
    await flushPromises()
    let navigation: Promise<unknown> | undefined
    if (leave === 'hidden') {
      vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
      document.dispatchEvent(new Event('visibilitychange'))
    } else if (leave === 'pagehide') window.dispatchEvent(new Event('pagehide'))
    else navigation = router.push('/plan')
    await flushPromises()
    release()
    await flushPromises()
    await navigation
    expect(commands).toEqual(['set.prepare', 'session.pause'])
    expect(training.session?.status).toBe('paused')
    wrapper.unmount()
  })

  it('offers pause during rest before the next automatic countdown', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('resting')
    current.flowVersion = 'watch-v1'
    const engine = new SessionEngine(current)
    await useTrainingStore().load(engine)
    const wrapper = await mountTraining(pinia)
    const pauseButton = wrapper.findAll('button').find((button) => button.text() === '暂停')
    expect(pauseButton).toBeDefined()
    await pauseButton!.trigger('click')
    await flushPromises()
    expect(engine.commands.some((command) => command.type === 'session.pause')).toBe(true)
    wrapper.unmount()
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('shows the action position and locks the ready CTA to 准备好了', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useTrainingStore().load(new SessionEngine(trainingSession('ready_to_continue', 1)))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    expect(wrapper.text()).toContain('动作 2 / 3')
    expect(wrapper.get('button.primary-action').text()).toBe('准备好了')
    wrapper.unmount()
  })

  it('labels a paused in-progress set as 继续训练 instead of restarting it', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useTrainingStore().load(new SessionEngine(trainingSession('paused')))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    expect(wrapper.get('button.primary-action').text()).toBe('继续训练')
    wrapper.unmount()
  })

  it('starts preparation even when optional countdown audio cannot initialize', async () => {
    vi.stubGlobal('AudioContext', class { constructor() { throw new Error('audio unavailable') } })
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.flowVersion = 'watch-v1'
    current.pauseReason = 'before_start'
    const engine = new SessionEngine(current)
    await useTrainingStore().load(engine)
    const wrapper = await mountTraining(pinia)
    await wrapper.get('button.primary-action').trigger('click')
    await flushPromises()
    expect(engine.commands.some(command => command.type === 'set.prepare')).toBe(true)
    wrapper.unmount()
  })

  it('loops the watch-flow reference without changing set progress', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('active')
    current.flowVersion = 'watch-v1'
    current.plan.items[0]!.sourceRef = { sourceId: source.id }
    current.plan.items[0]!.segment = { value: { start_seconds: 4, end_seconds: 12 }, source: 'video' }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const wrapper = await mountTraining(pinia)
    const media = wrapper.get<HTMLVideoElement>('video')
    media.element.currentTime = 12
    await media.trigger('timeupdate')
    expect(media.element.currentTime).toBe(4)
    expect(play).toHaveBeenCalled()
    expect(useTrainingStore().session?.progress[0]?.completedSets).toBe(0)
    wrapper.unmount()
  })

  it('makes rest a TrainPal-led focus without covering the reference video', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('resting')
    current.restStartedAt = new Date(Date.now() - 1_000).toISOString()
    current.restEndsAt = new Date(Date.now() + 30_000).toISOString()
    current.scheduledRestSeconds = 30
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    expect(wrapper.get('.rest-focus').text()).toContain('TrainPal')
    expect(wrapper.find('.media-stage .trainpal-coach').exists()).toBe(false)
    expect(wrapper.findAll('button.primary-action')).toHaveLength(1)
    expect(wrapper.get('button.primary-action').text()).toBe('提前继续')
    expect(wrapper.find('.trainpal-coach').exists()).toBe(false)
    expect(wrapper.text()).toContain('先放松呼吸')
    wrapper.unmount()
  })

  it('emits a motion cue only after a successful set command', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useTrainingStore().load(new CueEngine(trainingSession('paused', 0, 'hotblood')))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    await wrapper.get('button.primary-action').trigger('click')
    await flushPromises()

    expect(wrapper.getComponent(CoachMotion).props('cue')).toMatchObject({ event: 'set_started' })
    wrapper.unmount()

    const failedPinia = createPinia()
    setActivePinia(failedPinia)
    await useTrainingStore().load(new CueEngine(
      trainingSession('paused', 0, 'hotblood'),
      { fail: true },
    ))
    useAnalysisStore().sources = [source]
    const failedWrapper = await mountTraining(failedPinia)
    await failedWrapper.get('button.primary-action').trigger('click')
    await flushPromises()

    expect(failedWrapper.getComponent(CoachMotion).props('cue')).toBeNull()
    failedWrapper.unmount()
  })

  it('uses the session-completed cue instead of replaying set completion at the terminal', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useTrainingStore().load(new CueEngine(
      trainingSession('active', 1, 'hotblood'),
      { terminal: true },
    ))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    await wrapper.get('button.primary-action').trigger('click')
    await flushPromises()

    expect(wrapper.getComponent(CoachMotion).props('cue')).toMatchObject({
      event: 'session_completed',
    })
    wrapper.unmount()
  })

  it('triggers the challenger final-countdown cue once per rest window', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-21T00:00:00.000Z'))
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('resting', 0, 'challenger')
    current.restStartedAt = new Date().toISOString()
    current.restEndsAt = new Date(Date.now() + 10_000).toISOString()
    current.scheduledRestSeconds = 10
    await useTrainingStore().load(new CueEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)
    const initialCue = wrapper.getComponent(CoachMotion).props('cue')

    expect(initialCue).toMatchObject({ event: 'rest_final_countdown' })
    await vi.advanceTimersByTimeAsync(1_100)
    await flushPromises()
    expect(wrapper.getComponent(CoachMotion).props('cue')).toEqual(initialCue)
    wrapper.unmount()
  })

  it('shows a user-entered weight as part of the current set target', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.weightKg = { value: 12.5, source: 'user' }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    expect(wrapper.text()).toContain('12.5 kg')
    wrapper.unmount()
  })

  it('shows the original-video link from the immutable action snapshot', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
      originUrl: 'https://www.douyin.com/video/123456',
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    const link = wrapper.get('a.original-video-link')
    expect(link.text()).toBe('查看原视频')
    expect(link.attributes()).toMatchObject({
      href: 'https://www.douyin.com/video/123456',
      target: '_blank',
      rel: 'noopener noreferrer',
    })
    wrapper.unmount()
  })

  it('keeps training usable when the reference video cannot play', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    await wrapper.get('video').trigger('error')

    expect(wrapper.get('.media-placeholder[role="status"]').text()).toContain('参考视频暂时不可用')
    expect(wrapper.get('button.primary-action').text()).toBe('继续训练')
    wrapper.unmount()
  })

  it('plays a restored local Blob and still loads controlled sources after advancing', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('paused')
    current.plan.items[0]!.sourceRef = {
      sourceId: 'local:training-source',
      kind: 'local',
      title: '本地训练视频',
      localMedia: {
        fileName: 'training.mp4',
        mimeType: 'video/mp4',
        sizeBytes: 12,
        lastModified: 1,
        durationSeconds: 60,
      },
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 4, end_seconds: 12 },
      source: 'video',
    }
    current.plan.items[1]!.sourceRef = { sourceId: source.id, title: source.title }
    current.plan.items[1]!.segment = {
      value: { start_seconds: 20, end_seconds: 30 },
      source: 'video',
    }
    const training = useTrainingStore()
    await training.load(new SessionEngine(current))
    const localMedia = useLocalMediaStore()
    vi.spyOn(localMedia, 'resolve').mockResolvedValue('blob:restored-local-video')
    const analysis = useAnalysisStore()
    const loadSources = vi.spyOn(analysis, 'loadSources').mockImplementation(async () => {
      analysis.sources = [source]
    })
    const wrapper = await mountTraining(pinia)

    expect(wrapper.get('video').attributes('src')).toBe('blob:restored-local-video')
    expect(wrapper.text()).toContain('本地参考片段')
    expect(loadSources).not.toHaveBeenCalled()

    training.session = { ...current, currentItemIndex: 1 }
    await flushPromises()

    expect(loadSources).toHaveBeenCalledTimes(1)
    expect(wrapper.get('video').attributes('src')).toBe(source.media_url)
    wrapper.unmount()
  })

  it('stops an ordinary reference clip at its endpoint instead of looping it', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('active')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [{ ...source, duration_seconds: 60 }]
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const wrapper = await mountTraining(pinia)
    pause.mockClear()
    const media = wrapper.get<HTMLVideoElement>('video')
    Object.defineProperty(media.element, 'currentTime', {
      configurable: true,
      writable: true,
      value: 51,
    })
    await media.trigger('timeupdate')
    await flushPromises()

    expect(media.element.currentTime).toBe(51)
    expect(pause).toHaveBeenCalledTimes(1)
    expect(media.attributes('loop')).toBeUndefined()
    wrapper.unmount()
  })

  it('does not replay media that ended before the declared segment endpoint', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('active')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [{ ...source, duration_seconds: 50 }]
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const wrapper = await mountTraining(pinia)
    play.mockClear()
    const media = wrapper.get<HTMLVideoElement>('video')
    Object.defineProperties(media.element, {
      currentTime: { configurable: true, writable: true, value: 50 },
      ended: { configurable: true, get: () => true },
    })

    await media.trigger('ended')
    await media.trigger('loadedmetadata')
    await flushPromises()

    expect(media.element.currentTime).toBe(50)
    expect(play).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('clamps an overrun to the segment endpoint instead of jumping to its start', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const current = trainingSession('active')
    current.plan.items[0]!.sourceRef = {
      sourceId: source.id,
      title: source.title,
    }
    current.plan.items[0]!.segment = {
      value: { start_seconds: 41, end_seconds: 51 },
      source: 'video',
    }
    await useTrainingStore().load(new SessionEngine(current))
    useAnalysisStore().sources = [source]
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const wrapper = await mountTraining(pinia)
    pause.mockClear()
    const media = wrapper.get<HTMLVideoElement>('video')
    Object.defineProperty(media.element, 'currentTime', {
      configurable: true,
      writable: true,
      value: 51.25,
    })

    await media.trigger('loadedmetadata')
    await flushPromises()

    expect(media.element.currentTime).toBe(51)
    expect(pause).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('visibly pauses and stops ticking after another tab wins a revision conflict', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const engine = new SessionEngine(trainingSession('active'), true)
    await useTrainingStore().load(engine)
    useAnalysisStore().sources = [source]
    const wrapper = await mountTraining(pinia)

    await wrapper.get('button.pause-training').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('此页面已暂停')
    expect(wrapper.get('button.primary-action').attributes('disabled')).toBeDefined()

    await vi.advanceTimersByTimeAsync(1_100)
    await flushPromises()
    expect(engine.commands).toHaveLength(1)
    wrapper.unmount()
  })
})
