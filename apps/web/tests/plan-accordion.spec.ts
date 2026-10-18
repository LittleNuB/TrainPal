import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { DraftPlan } from '@/domain/types'
import { useDraftStore } from '@/stores/draft'
import { useTrainingStore } from '@/stores/training'
import PlanDraftView from '@/views/PlanDraftView.vue'

describe('direct action editing', () => {
  it('expands only one action and preserves direct values, including zero rest, after switching and reload', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    let saved: DraftPlan | undefined
    const repository = { load: async () => saved, save: async (plan: DraftPlan) => { saved = structuredClone(plan) } }
    const draft = useDraftStore()
    await draft.load(repository)
    draft.addManualAction({ name: '俯卧撑', mode: 'reps' })
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/plan', component: PlanDraftView },
      { path: '/train', component: { template: '<p>训练</p>' } },
      { path: '/personalize', component: { template: '<p>偏好</p>' } },
    ] })
    await router.push('/plan')
    const wrapper = mount(PlanDraftView, { global: { plugins: [pinia, router] } })
    expect(wrapper.findAll('.inline-action-editor')).toHaveLength(0)
    await wrapper.findAll('.action-summary')[0]!.trigger('click')
    expect(wrapper.findAll('.inline-action-editor')).toHaveLength(1)
    await wrapper.get('.inline-action-editor input[aria-label="每组次数"]').setValue('11')
    await wrapper.get('.inline-action-editor input[aria-label="休息秒数"]').setValue('0')
    await wrapper.findAll('.action-summary')[1]!.trigger('click')
    expect(wrapper.findAll('.inline-action-editor')).toHaveLength(1)
    expect(wrapper.get('.inline-action-editor').find('input[aria-label="每组秒数"]').exists()).toBe(true)
    expect(wrapper.findAll('.action-summary')[0]!.text()).toContain('11 次')
    await wrapper.findAll('.action-summary')[0]!.trigger('click')
    expect((wrapper.get('input[aria-label="每组次数"]').element as HTMLInputElement).value).toBe('11')
    expect(draft.items[0]!.restSeconds).toEqual({ value: 0, source: 'user' })
    await wrapper.get('input[aria-label="休息秒数"]').setValue('')
    expect(draft.items[0]!.restSeconds.value).toBeNull()
    await wrapper.findAll('.action-summary')[1]!.trigger('click')
    expect(wrapper.findAll('.plan-card')[0]!.get('.card-validation').text()).toContain('休息时间需为不小于 0 的整数')
    await wrapper.findAll('.action-summary')[0]!.trigger('click')
    expect(wrapper.get('input[aria-label="休息秒数"]').attributes('aria-invalid')).toBe('true')
    await wrapper.get('input[aria-label="休息秒数"]').setValue('0')
    expect(wrapper.findAll('.card-validation')).toHaveLength(0)
    useTrainingStore().validationIssues = [{ itemId: draft.items[0]!.id, field: 'weightKg', message: '重量只能是用户填写的正数' }]
    await wrapper.get('input[aria-label="休息秒数"]').setValue('5')
    expect(wrapper.get('.card-validation').text()).toContain('重量只能是用户填写的正数')
    await wrapper.get('input[aria-label="休息秒数"]').setValue('0')
    await draft.flushPersist()
    await draft.load(repository)
    await flushPromises()
    expect(draft.items[0]!.reps).toEqual({ value: 11, source: 'user' })
    expect(draft.items[0]!.restSeconds.value).toBe(0)
    wrapper.unmount()
  })
})
