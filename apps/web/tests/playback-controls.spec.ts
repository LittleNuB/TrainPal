import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PlaybackControls from '@/features/experience/PlaybackControls.vue'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { useTrainingStore } from '@/stores/training'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { requestPlaybackChoice } from '@/api/playback-client'
import type { TrainingSession } from '@/domain/training'

vi.mock('@/api/playback-client', () => ({ requestPlaybackChoice: vi.fn(), transcribeVoiceCommand: vi.fn() }))
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
  const save = vi.spyOn(training, 'selectPlayback').mockResolvedValue({ ok: true, session: training.session, record: null, events: [] })
  const wrapper = mount(PlaybackControls, { props: { video: null } })
  const button = (label: string) => wrapper.findAll('button').find((entry) => entry.text() === label)!
  const propose = async (text = '完整教学') => {
    await wrapper.get('input').setValue(text)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
  }
  return { training, wrapper, button, propose, save }
}

describe('bounded playback controls', () => {
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
    await button('取消，保持暂停').trigger('click')
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
    let finish!: () => void
    vi.spyOn(training, 'pause').mockImplementation(async () => {
      await new Promise<void>((resolve) => { finish = resolve })
      training.session!.status = 'paused'
      return { ok: true, session: training.session, record: null, events: [] }
    })
    await button('恢复完整教学').trigger('click')
    await flushPromises()
    await wrapper.get('details').trigger('toggle')
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
})
