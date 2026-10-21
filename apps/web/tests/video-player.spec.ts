import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VideoPlayer from '@/features/experience/VideoPlayer.vue'

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
})
afterEach(() => { vi.restoreAllMocks() })

describe('responsive video player', () => {
  it('reads real dimensions, preserves the whole image, and resets on replacement', async () => {
    const wrapper = mount(VideoPlayer, { props: { src: 'blob:portrait', label: '预览' } })
    const video = wrapper.get('video')
    Object.defineProperties(video.element, {
      videoWidth: { configurable: true, value: 1080 },
      videoHeight: { configurable: true, value: 1920 },
      duration: { configurable: true, value: 68 },
    })
    await video.trigger('loadedmetadata')
    expect(wrapper.classes()).toContain('video-player--portrait')
    expect(wrapper.attributes('style')).toContain('0.5625')
    expect(wrapper.text()).toContain('0:00 / 1:08')
    expect(wrapper.emitted('update:element')?.[0]?.[0]).toBe(video.element)
    await wrapper.setProps({ src: 'blob:landscape' })
    expect(wrapper.classes()).not.toContain('video-player--portrait')
    Object.defineProperties(video.element, {
      videoWidth: { configurable: true, value: 1920 },
      videoHeight: { configurable: true, value: 1080 },
    })
    await video.trigger('loadedmetadata')
    expect(wrapper.classes()).not.toContain('video-player--portrait')
    wrapper.unmount()
  })

  it('scrubs and replays only within the selected source segment', async () => {
    const wrapper = mount(VideoPlayer, { props: { src: 'blob:source', label: '动作', training: true,
      range: { start_seconds: 15, end_seconds: 27 } } })
    const video = wrapper.get('video')
    Object.defineProperty(video.element, 'duration', { value: 68 })
    await video.trigger('loadedmetadata')
    await wrapper.get('input[type="range"]').setValue('8')
    expect(video.element.currentTime).toBe(23)
    expect(wrapper.text()).toContain('0:08 / 0:12')
    await wrapper.findAll('button').find(button => button.text() === '重看本段')!.trigger('click')
    expect(video.element.currentTime).toBe(15)
    expect(wrapper.emitted('user-playback')).toEqual([[true]])
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('opens the same video in an accessible large view without pausing training, then restores focus', async () => {
    const background = document.createElement('main')
    document.body.append(background)
    const wrapper = mount(VideoPlayer, { attachTo: background, props: { src: 'blob:source', label: '动作', training: true } })
    const media = wrapper.get('video').element
    const trigger = wrapper.get<HTMLButtonElement>('.expand-video')
    trigger.element.focus()
    await trigger.trigger('click')
    await flushPromises()
    const dialog = document.querySelector('[role="dialog"]')!
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled()
    expect(dialog.textContent).not.toContain('训练已暂停')
    expect(dialog.querySelector('video')).toBe(media)
    expect(background.inert).toBe(true)
    expect(document.activeElement?.textContent).toContain('收起视频')
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    expect(document.activeElement?.getAttribute('aria-label')).toBe('关闭视频声音')
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement?.textContent).toContain('收起视频')
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(background.inert).toBeFalsy()
    expect(document.activeElement).toBe(trigger.element)
    expect(wrapper.get('video').element).toBe(media)
    expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled()
    expect(wrapper.emitted('layout-change')).toHaveLength(2)
    expect(document.body.style.overflow).not.toBe('hidden')
    wrapper.unmount()
  })

  it('does not restore a parent editor scroll lock after that editor unmounts', async () => {
    document.body.style.overflow = 'hidden'
    const wrapper = mount(VideoPlayer, { attachTo: document.body, props: { src: 'blob:source', label: '动作' } })
    await wrapper.get('.expand-video').trigger('click')
    await flushPromises()
    document.body.style.overflow = ''
    wrapper.unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('releases the expanded view when navigating away during opening', async () => {
    const wrapper = mount(VideoPlayer, { props: { src: 'blob:source', label: '动作' } })
    await wrapper.get('.expand-video').trigger('click')
    wrapper.unmount()
    await flushPromises()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.body.style.overflow).not.toBe('hidden')
  })
})
