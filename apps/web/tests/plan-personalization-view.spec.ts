import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { DraftPlan } from '@/domain/types'
import { actionFeedbackVersion } from '@/personalization/feedback'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import PlanDraftView from '@/views/PlanDraftView.vue'
import { requestPlanAdjustment } from '@/api/adjustment-client'

vi.mock('@/api/adjustment-client', async () => {
  const { proposePlanAdjustment } = await import('@/personalization/adjustment-policy')
  return { requestPlanAdjustment: vi.fn(async (input) => ({
    ...proposePlanAdjustment(input), policyVersion: 'adjustment-policy.v3', origin: 'model',
    summary: '合成测试建议',
  })) }
})

const setup = async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  let saved: DraftPlan | undefined
  const draft = useDraftStore()
  await draft.load({ load: async () => saved, save: async plan => { saved = structuredClone(plan) } })
  draft.addManualAction({ name: '深蹲', mode: 'reps' })
  draft.addManualAction({ name: '俯卧撑', mode: 'reps' })
  draft.plan.linkedPlanId = 'saved-plan'
  const library = useLibraryStore()
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/plan', component: PlanDraftView },
    { path: '/personalize', component: { template: '<p>偏好</p>' } },
  ] })
  await router.push('/plan')
  const wrapper = mount(PlanDraftView, { attachTo: document.body, global: { plugins: [pinia, router] } })
  const open = async () => {
    await wrapper.get('[data-adjustment-trigger]').trigger('click')
    if (wrapper.find('[data-edit-adjustment-intent]').exists()) await wrapper.get('[data-edit-adjustment-intent]').trigger('click')
    await wrapper.get('[data-adjustment-intent="more_challenging"]').trigger('click')
  }
  const generate = async () => {
    await wrapper.get('[data-generate-adjustment]').trigger('click')
    await flushPromises()
  }
  return { wrapper, draft, library, open, generate, pinia, router }
}

describe('personalization review boundaries', () => {
  it('does not display a saved legacy rule proposal as a model suggestion', async () => {
    const { wrapper, draft, open, generate, pinia, router } = await setup()
    await open()
    await generate()
    draft.adjustmentProposal!.policyVersion = 'adjustment-policy.v2'
    delete draft.adjustmentProposal!.origin
    wrapper.unmount()
    vi.mocked(requestPlanAdjustment).mockClear()
    const returned = mount(PlanDraftView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await returned.get('[data-adjustment-trigger]').trigger('click')
    expect(returned.find('.adjustment-change-list').exists()).toBe(false)
    expect(returned.find('[data-generate-adjustment]').exists()).toBe(true)
    expect(requestPlanAdjustment).not.toHaveBeenCalled()
    await returned.get('[data-generate-adjustment]').trigger('click')
    await flushPromises()
    expect(requestPlanAdjustment).toHaveBeenCalledTimes(1)
    expect(draft.adjustmentProposal?.origin).toBe('model')
    returned.unmount()
  })
  it('shows a saved proposal after remount and context changes without requesting again', async () => {
    const { wrapper, draft, library, open, generate, pinia, router } = await setup()
    await open()
    await generate()
    const saved = JSON.stringify(draft.adjustmentProposal)
    wrapper.unmount()
    library.profile.updatedAt = '2026-10-19T00:00:00Z'
    vi.mocked(requestPlanAdjustment).mockClear()
    const returned = mount(PlanDraftView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await returned.get('[data-adjustment-trigger]').trigger('click')
    expect(returned.find('.adjustment-change-list').exists()).toBe(true)
    expect(returned.get('.context-stale-note').text()).toContain('可以应用这份提案')
    expect(requestPlanAdjustment).not.toHaveBeenCalled()
    expect(JSON.stringify(draft.adjustmentProposal)).toBe(saved)
    returned.unmount()
  })
  it('stops displaying removed feedback without mutating the original proposal', async () => {
    const { wrapper, draft, library, open, generate } = await setup()
    const item = draft.items[0]!
    library.actionFeedback = [{ id: 'record:action', recordId: 'record', planId: 'saved-plan',
      itemId: item.id, actionVersion: actionFeedbackVersion(item), value: 'too_easy', recordedAt: '2026-10-09T00:00:00Z' }]
    await open()
    await generate()
    expect(wrapper.get('.adjustment-basis').text()).toContain('上次太轻')
    expect(wrapper.get('.adjustment-change-list').text()).toContain('最近“太轻”')
    const original = JSON.stringify(draft.adjustmentProposal)
    library.actionFeedback = []
    await flushPromises()
    expect(wrapper.get('.adjustment-basis').text()).not.toContain('上次太轻')
    expect(wrapper.get('.adjustment-basis').text()).not.toContain('参考 1 个')
    expect(wrapper.get('.adjustment-basis').text()).toContain('原反馈已更新或清除')
    expect(wrapper.get('.adjustment-change-list').text()).not.toContain('最近“太轻”')
    expect(JSON.stringify(draft.adjustmentProposal)).toBe(original)
    await wrapper.get('button[aria-label="关闭训练调整"]').trigger('click')
    await open()
    expect(wrapper.get('.adjustment-basis').text()).not.toContain('上次太轻')
    await generate()
    expect(draft.adjustmentProposal?.context?.signals).toEqual([])
    wrapper.unmount()
  })

  it('locks generation inputs until the selected training experience is saved', async () => {
    const { wrapper, draft, library, open, generate } = await setup()
    let release!: () => void
    const saving = new Promise<void>(resolve => { release = resolve })
    vi.spyOn(library, 'saveProfile').mockImplementation(async input => {
      await saving
      library.profile = { ...input, id: 'current', updatedAt: new Date().toISOString() }
    })
    await open()
    await wrapper.get('select[aria-label="训练经验"]').setValue('beginner')
    await generate()
    try {
      for (const input of wrapper.findAll('.adjustment-context select, .adjustment-context input, .adjustment-context button, [data-adjustment-intent], [data-adjustment-safety-stop], [data-generate-adjustment]')) {
        expect(input.attributes('disabled')).toBeDefined()
      }
      expect(draft.adjustmentProposal).toBeNull()
    } finally {
      release()
      await flushPromises()
    }
    expect(draft.adjustmentProposal?.context?.trainingExperience).toBe('beginner')
    expect(new Set(draft.adjustmentProposal?.changes.map(change => change.itemId)).size).toBe(1)
    wrapper.unmount()
  })

  it.each(['reduce_to_finish', 'extend_recovery'] as const)(
    'hides saved model summary and %s explanation after referenced feedback is cleared', async reason => {
      const { wrapper, draft, library, open, generate, pinia, router } = await setup()
      const item = draft.items[0]!
      library.actionFeedback = [{ id: 'record:action', recordId: 'record', planId: 'saved-plan',
        itemId: item.id, actionVersion: actionFeedbackVersion(item), value: 'too_hard', recordedAt: '2026-10-09T00:00:00Z' }]
      await open()
      await wrapper.get('[data-adjustment-intent="easier_to_finish"]').trigger('click')
      await generate()
      const proposal = draft.adjustmentProposal!
      proposal.summary = '根据上次太累的反馈调整了这次方案'
      for (const change of proposal.changes) {
        change.reasonCode = reason
        change.explanation = '上次太累，适当降低训练压力'
      }
      await flushPromises()
      expect(wrapper.get('.adjustment-dialog').text()).toContain(proposal.summary)
      expect(wrapper.get('.adjustment-change-list').text()).toContain('上次太累，适当降低训练压力')
      const original = JSON.stringify(proposal)
      library.actionFeedback = []
      await flushPromises()
      expect(wrapper.get('.adjustment-dialog').text()).not.toContain('上次太累')
      expect(wrapper.get('.adjustment-dialog').text()).toContain('原反馈已更新或清除')
      wrapper.unmount()
      vi.mocked(requestPlanAdjustment).mockClear()
      const returned = mount(PlanDraftView, { attachTo: document.body, global: { plugins: [pinia, router] } })
      await returned.get('[data-adjustment-trigger]').trigger('click')
      expect(returned.get('.adjustment-dialog').text()).not.toContain('上次太累')
      expect(JSON.stringify(draft.adjustmentProposal)).toBe(original)
      expect(requestPlanAdjustment).not.toHaveBeenCalled()
      returned.unmount()
    },
  )

  it('hides a saved no-change model summary when its feedback value is updated', async () => {
    const { wrapper, draft, library, open, generate, pinia, router } = await setup()
    const item = draft.items[0]!
    library.actionFeedback = [{ id: 'record:action', recordId: 'record', planId: 'saved-plan',
      itemId: item.id, actionVersion: actionFeedbackVersion(item), value: 'too_hard', recordedAt: '2026-10-09T00:00:00Z' }]
    await open()
    await generate()
    draft.adjustmentProposal!.status = 'no_change'
    draft.adjustmentProposal!.changes = []
    draft.adjustmentProposal!.summary = '根据上次太累的反馈，保留原方案'
    const original = JSON.stringify(draft.adjustmentProposal)
    wrapper.unmount()
    const returned = mount(PlanDraftView, { attachTo: document.body, global: { plugins: [pinia, router] } })
    await returned.get('[data-adjustment-trigger]').trigger('click')
    expect(returned.get('.adjustment-dialog').text()).toContain('根据上次太累的反馈')
    library.actionFeedback[0]!.value = 'just_right'
    await flushPromises()
    expect(returned.get('.adjustment-dialog').text()).not.toContain('上次太累')
    expect(JSON.stringify(draft.adjustmentProposal)).toBe(original)
    returned.unmount()
  })

  it('preserves invalid review errors across collapse and focuses the first error before apply', async () => {
    const { wrapper, draft, open, generate } = await setup()
    await open()
    await generate()
    const original = JSON.stringify(draft.items)
    await wrapper.get('.adjustment-change-list input').setValue('0')
    await wrapper.findAll('.review-summary')[1]!.trigger('click')
    expect(wrapper.findAll('.adjustment-change-list article')[0]!.text()).toContain('请检查每组次数')
    await wrapper.get('[data-apply-adjustment]').trigger('click')
    expect(wrapper.findAll('.review-summary')[0]!.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.adjustment-change-list input').attributes('aria-invalid')).toBe('true')
    expect(document.activeElement).toBe(wrapper.get('.adjustment-change-list input').element)
    expect(JSON.stringify(draft.items)).toBe(original)
    await wrapper.get('.adjustment-change-list input').setValue('11')
    expect(wrapper.findAll('.review-number-error')).toHaveLength(0)
    wrapper.unmount()
  })
})
