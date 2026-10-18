import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PlaybackControls from '@/features/experience/PlaybackControls.vue'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { useTrainingStore } from '@/stores/training'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { requestPlaybackChoice } from '@/api/playback-client'
import type { TrainingSession } from '@/domain/training'
import { localWakeAvailability, startLocalWake } from '@/training/voice-wake'
import { captureVoiceCommand } from '@/training/voice-command'

vi.mock('@/api/playback-client', () => ({ requestPlaybackChoice: vi.fn(), transcribeVoiceCommand: vi.fn() }))
vi.mock('@/training/voice-wake', () => ({ localWakeAvailability: vi.fn(), installLocalWake: vi.fn(), startLocalWake: vi.fn() }))
vi.mock('@/training/voice-command', () => ({ captureVoiceCommand: vi.fn() }))

// jsdom has no native dialog implementation; focus trapping is covered in browser tests.
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
})
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

const setup = () => {
  setActivePinia(createPinia())
  const training = useTrainingStore()
  const item = createQuickExperienceDraftItems()[0]!
  item.sourceRef = { sourceId: 'video' }
  item.segment = { value: { start_seconds: 0, end_seconds: 20 }, source: 'video' }
  training.session = { id: 'current', sessionId: 'session', revision: 1, status: 'paused',
    plan: { name: '方案', source: 'saved', sourcePlanId: 'plan', items: [item] },
    currentItemIndex: 0, currentSetIndex: 0, progress: [],
  } as unknown as TrainingSession
  vi.mocked(requestPlaybackChoice).mockResolvedValue('clip-0')
  vi.spyOn(useDraftStore(), 'flushPersist').mockResolvedValue()
  vi.spyOn(useDraftStore(), 'reload').mockResolvedValue()
  vi.spyOn(useLibraryStore(), 'reload').mockResolvedValue()
  vi.spyOn(useLibraryStore(), 'refreshHistory').mockResolvedValue()
  const save = vi.spyOn(training, 'selectPlayback').mockResolvedValue({ ok: true, session: training.session, record: null, events: [] })
  const wrapper = mount(PlaybackControls, { props: { video: null } })
  const button = (label: string) => wrapper.findAll('button').find((entry) => entry.text() === label)!
  const propose = async (text = '完整教学') => {
    if (!wrapper.find('.voice-panel').exists()) await button('跟我说').trigger('click')
    if (!wrapper.find('input').exists()) await button('用文字').trigger('click')
    await wrapper.get('input').setValue(text)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
  }
  return { training, wrapper, button, propose, save }
}

describe('bounded playback controls', () => {
  it('reports unavailable rewind without video and seeks only inside the current clip', async () => {
    const { wrapper, propose } = setup()
    await propose('回退 5 秒')
    expect(wrapper.text()).toContain('这个动作没有可回看的视频')
    expect(wrapper.text()).not.toContain('已回退')
    expect(wrapper.emitted('resume')).toBeUndefined()
    const video = document.createElement('video')
    video.currentTime = 8
    await wrapper.setProps({ video })
    await propose('回退 5 秒')
    expect(video.currentTime).toBe(3)
    expect(wrapper.text()).toContain('已回退 5 秒')
    await propose('回退 5 秒')
    expect(video.currentTime).toBe(0)
    expect(wrapper.text()).toContain('已回到片段开头')
    wrapper.unmount()
  })

  it('keeps unsupported wake optional and leaves text commands available', async () => {
    const { wrapper, button } = setup()
    vi.mocked(localWakeAvailability).mockResolvedValue('unavailable')
    vi.mocked(startLocalWake).mockClear()
    await button('语音唤醒 · 关').trigger('click')
    await button('开启语音唤醒').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('暂不支持语音唤醒')
    expect(startLocalWake).not.toHaveBeenCalled()
    await button('用文字').trigger('click')
    await wrapper.get('input').setValue('继续训练')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.emitted('resume')).toHaveLength(1)
    expect(wrapper.find('.voice-panel').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows listening on wake and discards a command after closing that request', async () => {
    const { wrapper, button } = setup()
    let listener!: Parameters<typeof startLocalWake>[0]
    vi.mocked(localWakeAvailability).mockResolvedValue('available')
    vi.mocked(startLocalWake).mockImplementation((callbacks) => { listener = callbacks; callbacks.onReady(); return vi.fn() })
    await button('语音唤醒 · 关').trigger('click')
    await button('开启语音唤醒').trigger('click')
    await flushPromises()
    await wrapper.get('[aria-label="关闭 TrainPal"]').trigger('click')
    const previous = listener
    previous.onWake()
    await flushPromises()
    expect(wrapper.get('h2').text()).toBe('我在听')
    await wrapper.get('[aria-label="关闭 TrainPal"]').trigger('click')
    previous.onCommand('继续训练')
    await flushPromises()
    expect(wrapper.emitted('resume')).toBeUndefined()
    expect(wrapper.find('.voice-panel').exists()).toBe(false)
    wrapper.unmount()
  })

  it('preserves new manual values after unmounting during a clip save', async () => {
    const { wrapper, button, propose, save, training } = setup()
    const draft = useDraftStore()
    vi.mocked(draft.reload).mockRestore()
    const stored = JSON.parse(JSON.stringify({ id: 'current', linkedPlanId: 'plan',
      name: '方案', items: training.session!.plan.items, updatedAt: '2026-09-19T00:00:00Z' }))
    await draft.load({ load: async () => stored, save: async () => undefined })
    let finish!: () => void
    save.mockImplementation(async () => {
      await new Promise<void>((resolve) => { finish = resolve })
      return { ok: true, session: training.session, record: null, events: [] }
    })
    await propose()
    await button('用这段并继续').trigger('click')
    await flushPromises()
    wrapper.unmount()
    draft.updateValue(draft.items[0]!.id, 'reps', 19)
    finish()
    await flushPromises()
    expect(draft.items[0]!.reps).toEqual({ value: 19, source: 'user' })
  })
  it('ignores a response arriving after cancellation without changing or resuming the clip', async () => {
    const { wrapper, button, propose, save, training } = setup()
    const before = { ...training.session!.plan.items[0]!.segment.value }
    let respond!: (id: string) => void
    vi.mocked(requestPlaybackChoice).mockImplementation(() => new Promise((resolve) => { respond = resolve }))
    await propose()
    await button('取消').trigger('click')
    respond('clip-0')
    await flushPromises()
    expect(wrapper.find('.clip-proposal').exists()).toBe(false)
    expect(save).not.toHaveBeenCalled()
    expect(training.session!.plan.items[0]!.segment.value).toEqual(before)
    expect(wrapper.emitted('resume')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps the original clip paused after timeout or storage failure', async () => {
    const { wrapper, button, propose, save } = setup()
    vi.useFakeTimers()
    vi.mocked(requestPlaybackChoice).mockImplementation((_name, _text, _options, signal) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('timeout')), { once: true })
    }))
    await propose()
    await vi.advanceTimersByTimeAsync(17_001)
    await flushPromises()
    expect(wrapper.text()).toContain('查找已停止')
    expect(save).not.toHaveBeenCalled()
    vi.mocked(requestPlaybackChoice).mockResolvedValue('clip-0')
    await propose()
    save.mockResolvedValue({ ok: false, code: 'storage_unavailable', message: '保存失败', session: useTrainingStore().session })
    await button('用这段并继续').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('保存失败')
    expect(wrapper.emitted('resume')).toBeUndefined()
    wrapper.unmount()
  })

  it('does not restore a clip after closing while the pause is still pending', async () => {
    const { wrapper, button, training, save } = setup()
    training.session!.plan.items[0]!.playbackSelection = { start_seconds: 0, end_seconds: 20 }
    training.session!.status = 'active'
    await flushPromises()
    await button('跟我说').trigger('click')
    let finish!: () => void
    vi.spyOn(training, 'pause').mockImplementation(async () => {
      await new Promise<void>((resolve) => { finish = resolve })
      training.session!.status = 'paused'
      return { ok: true, session: training.session, record: null, events: [] }
    })
    await button('用文字').trigger('click')
    await button('恢复完整教学').trigger('click')
    await flushPromises()
    await wrapper.get('[aria-label="关闭 TrainPal"]').trigger('click')
    finish()
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    expect(wrapper.emitted('resume')).toBeUndefined()
    wrapper.unmount()
  })
  it('does not resume when the user hides the page during an accepted save', async () => {
    const { wrapper, button, propose, save } = setup()
    let finish!: () => void
    save.mockImplementation(async () => {
      await new Promise<void>((resolve) => { finish = resolve })
      return { ok: true, session: useTrainingStore().session, record: null, events: [] }
    })
    await propose()
    await button('用这段并继续').trigger('click')
    await flushPromises()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    finish()
    await flushPromises()
    expect(wrapper.emitted('resume')).toBeUndefined()
    wrapper.unmount()
  })

  it('rejects a stale action after pending draft writes finish', async () => {
    const { wrapper, training, button, propose, save } = setup()
    let finish!: () => void
    vi.mocked(useDraftStore().flushPersist).mockImplementation(() => new Promise<void>((resolve) => { finish = resolve }))
    await propose()
    await button('用这段并继续').trigger('click')
    await flushPromises()
    training.session!.revision++
    finish()
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    expect(wrapper.emitted('resume')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps training paused and avoids a model call for a pain report', async () => {
    const { wrapper, propose } = setup()
    vi.mocked(requestPlaybackChoice).mockClear()
    await propose('膝盖疼，帮我调整一下')
    expect(requestPlaybackChoice).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('先停止训练')
    expect(wrapper.emitted('resume')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps text secondary and requires an explicit voice action before recording', async () => {
    const { wrapper, button } = setup()
    vi.mocked(captureVoiceCommand).mockClear()
    expect(wrapper.find('input').exists()).toBe(false)
    expect(captureVoiceCommand).not.toHaveBeenCalled()
    await button('跟我说').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('语音与隐私')
    expect(captureVoiceCommand).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('routes wake requests to review cards and accepts only a fresh explicit voice confirmation', async () => {
    const { wrapper, button, save } = setup()
    let listener!: Parameters<typeof startLocalWake>[0]
    const stop = vi.fn()
    vi.mocked(startLocalWake).mockImplementation((callbacks) => { listener = callbacks; callbacks.onReady(); return stop })
    vi.mocked(localWakeAvailability).mockResolvedValue('available')
    await button('语音唤醒 · 关').trigger('click')
    await button('开启语音唤醒').trigger('click')
    await flushPromises()
    listener.onWake()
    listener.onCommand('重看呼吸讲解')
    await flushPromises()
    expect(wrapper.find('.clip-proposal').exists()).toBe(true)
    expect(save).not.toHaveBeenCalled()
    listener.onWake()
    listener.onCommand('确认')
    await flushPromises()
    expect(save).toHaveBeenCalledOnce()
    expect(wrapper.emitted('resume')).toHaveLength(1)
    wrapper.unmount()
    expect(stop).toHaveBeenCalled()
  })

  it('rejects stale voice confirmation and turns off listening when hidden', async () => {
    const { wrapper, button, save, training } = setup()
    let listener!: Parameters<typeof startLocalWake>[0]
    const stop = vi.fn()
    vi.mocked(startLocalWake).mockImplementation((callbacks) => { listener = callbacks; return stop })
    vi.mocked(localWakeAvailability).mockResolvedValue('available')
    await button('语音唤醒 · 关').trigger('click')
    await button('开启语音唤醒').trigger('click')
    await flushPromises()
    listener.onWake()
    listener.onCommand('重看呼吸讲解')
    await flushPromises()
    training.session!.revision++
    listener.onWake()
    listener.onCommand('确认')
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(wrapper.emitted('handsfree')?.at(-1)).toEqual([false])
    expect(stop).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not start listening after a late capability result following unmount', async () => {
    const { wrapper, button } = setup()
    let resolve!: (value: 'available') => void
    vi.mocked(localWakeAvailability).mockImplementation(() => new Promise((done) => { resolve = done }))
    vi.mocked(startLocalWake).mockClear()
    await button('语音唤醒 · 关').trigger('click')
    await button('开启语音唤醒').trigger('click')
    wrapper.unmount()
    resolve('available')
    await flushPromises()
    expect(startLocalWake).not.toHaveBeenCalled()
  })

  it('keeps raw browser failures out of product copy', async () => {
    const { wrapper, propose } = setup()
    vi.mocked(requestPlaybackChoice).mockRejectedValue(new Error('Internal transport debug: synthetic trace'))
    await propose()
    expect(wrapper.text()).toContain('这次没能完成，请重试')
    expect(wrapper.text()).not.toContain('Internal transport')
    expect(wrapper.text()).not.toContain('synthetic trace')
    wrapper.unmount()
  })

  it('does not send numerical training adjustments to the clip selection model', async () => {
    const { wrapper, propose } = setup()
    vi.mocked(requestPlaybackChoice).mockClear()
    await propose('训练量减少两组')
    expect(requestPlaybackChoice).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('动作卡片中修改')
    wrapper.unmount()
  })
})
