import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { expect, it, vi } from 'vitest'
import { useDraftStore } from '@/stores/draft'
import TrainHubView from '@/views/TrainHubView.vue'

it('does not claim a draft was saved when entering the hub fails to save it', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const draft = useDraftStore()
  draft.addManualAction({ name: '未保存动作', mode: 'reps' })
  vi.spyOn(draft, 'flushPersist').mockRejectedValue(new Error('storage unavailable'))
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/train', component: TrainHubView },
    { path: '/:pathMatch(.*)*', component: { template: '<p />' } },
  ] })
  await router.push('/train')
  const wrapper = mount(TrainHubView, { global: { plugins: [pinia, router] } })
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('训练方案暂时没有读取成功')
    expect(wrapper.text()).not.toContain('方案已保存在本机')
    expect(draft.items[0]?.name).toBe('未保存动作')
  } finally {
    wrapper.unmount()
    vi.restoreAllMocks()
  }
})
