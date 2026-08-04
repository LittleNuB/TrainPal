import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import App from '@/App.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { useAppBootstrapStore } from '@/stores/app-bootstrap'
import { useTrainingStore } from '@/stores/training'
import type { TrainingCommand, TrainingEngine, TrainingEngineResult } from '@/training/training-engine'

const restoredSession = (): NonNullable<TrainingEngineResult['session']> => ({
  id: 'current',
  sessionId: 'session-1',
  revision: 1,
  status: 'paused',
  pauseReason: 'recovered',
  plan: { name: '手臂训练', source: 'draft', sourcePlanId: null, items: [] },
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
  updatedAt: '2026-07-21T00:01:00.000Z',
})

class RestoredTrainingEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    return { ok: true, session: restoredSession(), record: null, events: [] }
  }

  async dispatch(_command: TrainingCommand): Promise<TrainingEngineResult> {
    throw new Error('not used')
  }
}

class UnavailableTrainingEngine implements TrainingEngine {
  async restore(): Promise<TrainingEngineResult> {
    return {
      ok: false,
      code: 'storage_unavailable',
      message: '本机训练数据暂时无法读取',
      session: null,
    }
  }

  async dispatch(_command: TrainingCommand): Promise<TrainingEngineResult> {
    throw new Error('not used')
  }
}

describe('应用本机数据启动壳', () => {
  it('shows the three-item navigation only for top-level route meta', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/',
          name: 'home',
          component: { template: '<p>首页</p>' },
          meta: { showBottomNav: true, theme: 'journal' },
        },
        {
          path: '/analysis',
          name: 'analysis',
          component: { template: '<p>分析</p>' },
          meta: { showBottomNav: false, theme: 'journal' },
        },
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    await useAppBootstrapStore().initialize(async () => undefined)
    await flushPromises()

    const navigation = wrapper.get('[aria-label="主要导航"]')
    expect(navigation.findAll('a').map((link) => link.text())).toEqual([
      '01首页',
      '02训练',
      '03我的',
    ])

    await router.push('/analysis')
    await flushPromises()
    expect(wrapper.find('[aria-label="主要导航"]').exists()).toBe(false)
  })

  it('mounts a recoverable error shell when IndexedDB bootstrap fails', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<p>首页已就绪</p>' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    const bootstrap = useAppBootstrapStore()
    let attempts = 0

    await bootstrap.initialize(async () => {
      attempts += 1
      if (attempts === 1) throw new Error('indexeddb unavailable')
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('本机训练数据暂时无法读取')
    expect(wrapper.text()).toContain('把本地健身视频转成可确认、可调整、可完成的训练')
    expect(wrapper.text()).not.toContain('indexeddb unavailable')

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(attempts).toBe(2)
    expect(wrapper.text()).toContain('首页已就绪')
  })

  it('treats a failed training-session restore as a bootstrap failure', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<p>首页已就绪</p>' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })

    await useAppBootstrapStore().initialize(async () => {
      await useTrainingStore().load(new UnavailableTrainingEngine())
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('本机训练数据暂时无法读取')
    expect(wrapper.text()).not.toContain('首页已就绪')
  })

  it('keeps unfinished training reachable outside the active training page and training hub', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/',
          name: 'home',
          component: { template: '<p>视频页</p>' },
          meta: { showTrainingTask: true },
        },
        {
          path: '/plan',
          name: 'plan',
          component: { template: '<p>方案页</p>' },
          meta: { showTrainingTask: true },
        },
        {
          path: '/mine',
          name: 'mine',
          component: { template: '<p>我的训练</p>' },
          meta: { showTrainingTask: true },
        },
        {
          path: '/train',
          name: 'train',
          component: { template: '<p>训练中心</p>' },
          meta: { showTrainingTask: false },
        },
        {
          path: '/training',
          name: 'training',
          component: { template: '<p>训练中</p>' },
          meta: { showTrainingTask: false },
        },
      ],
    })
    await router.push('/plan')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    await useAppBootstrapStore().initialize(async () => {
      await useTrainingStore().load(new RestoredTrainingEngine())
    })
    await flushPromises()

    expect(wrapper.get('.global-training-entry').text()).toContain('继续训练')

    await router.push('/mine')
    await flushPromises()

    expect(wrapper.get('.global-training-entry').text()).toContain('继续训练')

    await router.push('/train')
    await flushPromises()

    expect(wrapper.find('.global-training-entry').exists()).toBe(false)

    await router.push('/training')
    await flushPromises()

    expect(wrapper.find('.global-training-entry').exists()).toBe(false)
  })

  it('keeps a running analysis reachable outside the analysis page, including during training', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/analysis',
          name: 'analysis',
          component: { template: '<p>分析页</p>' },
          meta: { showAnalysisTask: false },
        },
        {
          path: '/training',
          name: 'training',
          component: { template: '<p>训练中</p>' },
          meta: { showAnalysisTask: true },
        },
      ],
    })
    await router.push('/training')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    await useAppBootstrapStore().initialize(async () => undefined)
    useAnalysisStore().status = 'running'
    await flushPromises()

    expect(wrapper.get('.global-analysis-entry').text()).toContain('正在建立分析请求')

    await router.push('/analysis')
    await flushPromises()

    expect(wrapper.find('.global-analysis-entry').exists()).toBe(false)
  })
})
