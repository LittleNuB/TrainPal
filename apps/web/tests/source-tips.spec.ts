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
