import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TrainingActions from '@/features/experience/TrainingActions.vue'

describe('training action focus', () => {
  it('restores keyboard focus after an async command replaces the disabled action', async () => {
    const wrapper = mount(TrainingActions, { attachTo: document.body, props: {
      status: 'active', mode: 'reps', startLabel: '继续训练', disabled: false, expanded: true,
    } })
    wrapper.get<HTMLButtonElement>('.primary-action').element.focus()
    await wrapper.setProps({ disabled: true })
    await wrapper.setProps({ status: 'resting' })
    expect(wrapper.get<HTMLButtonElement>('.primary-action').element.disabled).toBe(true)
    await wrapper.setProps({ disabled: false })
    await flushPromises()
    expect(document.activeElement).toBe(wrapper.get('.primary-action').element)
    expect(document.activeElement?.textContent).toBe('提前继续')
    wrapper.unmount()
  })
})
