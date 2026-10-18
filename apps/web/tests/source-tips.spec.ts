import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import SourceTips from '@/features/experience/SourceTips.vue'
import type { SourceTip } from '@/domain/types'

it('keeps the chosen tip through cloned session ticks and resets only for changed content', async () => {
  const tips: SourceTip[] = ['收紧腹部', '推起时呼气'].map(text => ({ text, category: 'breathing',
    evidence: { type: 'speech', start_seconds: 1, end_seconds: 3 } }))
  const wrapper = mount(SourceTips, { props: { tips } })
  await wrapper.get('button').trigger('click')
  await wrapper.setProps({ tips: structuredClone(tips) })
  expect(wrapper.get('p').text()).toBe('推起时呼气')
  await wrapper.setProps({ tips: [tips[0]!] })
  expect(wrapper.get('p').text()).toBe('收紧腹部')
})

it('lets the user approve individual source tips without implicitly approving the others', async () => {
  const tips: SourceTip[] = ['收紧腹部', '推起时呼气'].map(text => ({ text, category: 'breathing',
    evidence: { type: 'speech', start_seconds: 1, end_seconds: 3 } }))
  const wrapper = mount(SourceTips, { props: { tips, review: { enabled: true, approvedIndexes: [] } } })
  const checkboxes = wrapper.findAll<HTMLInputElement>('input[type="checkbox"]')
  expect(checkboxes).toHaveLength(2)
  expect(checkboxes.map(box => box.element.checked)).toEqual([false, false])
  await checkboxes[1]!.setValue(true)
  expect(wrapper.emitted('review')).toEqual([[[1]]])
  await wrapper.setProps({ review: { enabled: false, approvedIndexes: [] } })
  expect(checkboxes.every(box => box.element.disabled)).toBe(true)
  expect(wrapper.text()).toContain('先确认动作，再核对要点')
})
