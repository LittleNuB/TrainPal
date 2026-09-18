import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import GymtiQuestionStep from '@/features/gymti/GymtiQuestionStep.vue'

const question = {
  id: 'q-energy',
  prompt: '今天本来计划训练，但下班后灵魂只剩 3% 电。你会？',
  context: '选择更接近此刻真实反应的一项',
  options: [
    { id: 'keep-plan', label: '计划就是计划，哪怕只练 30 分钟也要去。' },
    { id: 'small-start', label: '给我一个“只要动一动也算赢”的入口。' },
  ],
}

describe('GYMTI question step', () => {
  it('submits on the first option click and locks duplicate input immediately', async () => {
    const wrapper = mount(GymtiQuestionStep, {
      props: { question, busy: false, failure: false },
    })
    const option = wrapper.findAll('[data-option-id]')[0]!

    await option.trigger('click')
    await option.trigger('click')

    expect(wrapper.emitted('select')).toEqual([['keep-plan']])
    expect(wrapper.findAll('button[data-option-id]').every((button) =>
      button.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.text()).toContain('TrainPal 正在挑下一题…')
    expect(wrapper.text()).not.toContain('继续')
  })

  it('keeps the current question visible on a contract failure and offers retry or exit', async () => {
    const wrapper = mount(GymtiQuestionStep, {
      props: { question, busy: false, failure: true },
    })

    expect(wrapper.text()).toContain(question.prompt)
    expect(wrapper.text()).toContain('这一步暂时没能完成')
    await wrapper.get('[data-action="retry"]').trigger('click')
    await wrapper.get('[data-action="exit"]').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
    expect(wrapper.emitted('exit')).toHaveLength(1)
  })

  it('shows a previous answer while allowing the user to choose it again', async () => {
    const wrapper = mount(GymtiQuestionStep, {
      props: {
        question,
        busy: false,
        failure: false,
        answeredOptionId: 'keep-plan',
      },
    })

    expect(wrapper.get('[data-option-id="keep-plan"]').attributes('aria-checked')).toBe('true')
    expect(wrapper.get('[data-option-id="small-start"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-option-id="small-start"]').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['small-start']])
  })
})
