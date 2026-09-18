import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { useLocalMediaStore } from '@/stores/local-media'

describe('TrainPal 首页', () => {
  it('keeps upload explicit and starts analysis only from the primary action', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    vi.spyOn(analysis, 'restore').mockResolvedValue()
    vi.spyOn(analysis, 'loadCapabilities').mockImplementation(async () => {
      analysis.capabilities = {
        local_upload_enabled: true,
        local_analysis_max_seconds: 60,
        local_upload_max_bytes: 25_000_000,
      }
    })
    vi.spyOn(analysis, 'loadSources').mockResolvedValue()
    const start = vi.spyOn(analysis, 'start').mockResolvedValue()

    const file = new File(['fixture'], '今天练肩.mp4', {
      type: 'video/mp4',
      lastModified: 7,
    })
    const record = {
      sourceId: 'local:home-fixture',
      blob: file,
      fileName: file.name,
      mimeType: file.type,
      sizeBytes: file.size,
      lastModified: file.lastModified,
      durationSeconds: 38,
      importedAt: '2026-07-22T00:00:00.000Z',
      updatedAt: '2026-07-22T00:00:00.000Z',
    }
    const localMedia = useLocalMediaStore()
    localMedia.current = record
    vi.spyOn(localMedia, 'urlFor').mockReturnValue('blob:home-fixture')

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: HomeView },
        { path: '/analysis', component: { template: '<p>分析页</p>' } },
        { path: '/plan', component: { template: '<p>方案页</p>' } },
        { path: '/train', component: { template: '<p>训练中心</p>' } },
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(HomeView, { global: { plugins: [pinia, router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('把想练的视频，变成你的训练。')
    expect(wrapper.text()).not.toContain('不是看完一条视频')
    expect(wrapper.text()).not.toContain('带来你的参考')
    expect(wrapper.text()).not.toContain('每个关键判断')
    expect(wrapper.text()).toContain('最长 1 分钟')
    expect(wrapper.text()).toContain('结果需要核对')
    expect(wrapper.text()).toContain('最大 25.0 MB')
    expect(wrapper.text()).toContain('原视频保存在当前设备；服务端临时副本只用于本次分析')
    expect(wrapper.get('video').attributes('src')).toBe('blob:home-fixture')
    expect(start).not.toHaveBeenCalled()

    analysis.status = 'running'
    await flushPromises()
    expect(wrapper.get('.start-analysis').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="status"]').text()).toContain('明确取消')

    analysis.status = 'idle'
    await flushPromises()

    await wrapper.get('.start-analysis').trigger('click')
    await flushPromises()

    expect(start).toHaveBeenCalledWith(expect.objectContaining({
      sourceId: 'local:home-fixture',
      file: expect.any(File),
    }))
    expect(router.currentRoute.value.path).toBe('/analysis')
  })

  it('按时长展示脱敏真实来源，仅在用户确认后开始分析', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const analysis = useAnalysisStore()
    vi.spyOn(analysis, 'restore').mockResolvedValue()
    vi.spyOn(analysis, 'loadCapabilities').mockImplementation(async () => {
      analysis.capabilities = {
        local_upload_enabled: true,
        local_analysis_max_seconds: 300,
        local_upload_max_bytes: 256 * 1024 * 1024,
      }
    })
    vi.spyOn(analysis, 'loadSources').mockImplementation(async () => {
      analysis.sources = [
        { id: 'z-108', title: '不得显示标题 Z', media_url: '/z', duration_seconds: 108.72 },
        { id: 'b-68', title: '不得显示标题 B', media_url: '/b', duration_seconds: 67.83 },
        { id: 'c-208', title: '不得显示标题 C', media_url: '/c', duration_seconds: 207.77 },
        { id: 'd-100', title: '不得显示标题 D', media_url: '/d', duration_seconds: 100.43 },
        { id: 'x-299', title: '不得显示标题 X', media_url: '/x', duration_seconds: 299 },
        { id: 'e-55', title: '不得显示标题 E', media_url: '/e', duration_seconds: 54.87 },
      ]
    })
    const start = vi.spyOn(analysis, 'start').mockResolvedValue()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: HomeView },
        { path: '/analysis', component: { template: '<p>分析页</p>' } },
        { path: '/plan', component: { template: '<p>方案页</p>' } },
        { path: '/train', component: { template: '<p>训练中心</p>' } },
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(HomeView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] },
    })
    await flushPromises()

    expect(wrapper.get('summary').text()).toContain('先用示例视频试试')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.text()).toContain('最长 5 分钟')
    expect(wrapper.text()).toContain('最大 268.4 MB')
    expect(wrapper.text()).not.toContain('评委入口')
    expect(wrapper.text()).not.toContain('体验码')
    expect(wrapper.findAll('.quick-real-source').map((source) => source.text())).toEqual([
      '0:55',
      '1:08',
      '1:40',
      '1:49',
      '3:28',
    ])
    expect(wrapper.text()).not.toContain('不得显示标题')

    const firstSource = wrapper.get<HTMLButtonElement>('.quick-real-source')
    firstSource.element.focus()
    await firstSource.trigger('click')
    await flushPromises()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(dialog.text()).toContain('真实 AI 分析')
    expect(dialog.text()).toContain('约 1 分钟')
    expect(dialog.text()).toContain('结果需要核对')
    expect(document.activeElement).toBe(wrapper.get<HTMLButtonElement>('.quick-real-cancel').element)
    await wrapper.get('.quick-real-cancel').trigger('click')
    await flushPromises()

    expect(start).not.toHaveBeenCalled()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(firstSource.element)

    analysis.status = 'running'
    await flushPromises()
    expect(wrapper.findAll('.quick-real-source').every((source) => (
      source.attributes('disabled') !== undefined
    ))).toBe(true)

    analysis.status = 'idle'
    await flushPromises()

    await firstSource.trigger('click')
    await flushPromises()
    await wrapper.get('.quick-real-confirm').trigger('click')
    await flushPromises()

    expect(start).toHaveBeenCalledTimes(1)
    expect(start).toHaveBeenCalledWith(expect.objectContaining({ sourceId: 'e-55' }))
    expect(router.currentRoute.value.path).toBe('/analysis')
    wrapper.unmount()
  })
})
