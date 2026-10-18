import 'fake-indexeddb/auto'
import { webcrypto } from 'node:crypto'
import Dexie from 'dexie'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { fingerprintTrainingReminderInput } from '@/api/reminder-fingerprint'
import { createDexieDraftRepository } from '@/db/draft-repository'
import { createHachimiDatabase, type HachimiDatabase } from '@/db/hachimi-database'
import { createDexieLibraryRepository } from '@/db/library-repository'
import { createDexieTrainingRepository } from '@/db/training-repository'
import { estimateCalories } from '@/features/experience/calorie'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'
import { createTrainingEngine } from '@/training/training-engine'
import TrainingView from '@/views/TrainingView.vue'
import type { AnalysisCandidate } from '@/domain/types'

const databases: HachimiDatabase[] = []
const wrappers: VueWrapper[] = []
beforeEach(() => vi.stubGlobal('crypto', webcrypto))
afterEach(async () => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.useRealTimers()
  vi.unstubAllGlobals()
  sessionStorage.clear()
  for (const db of databases.splice(0)) { db.close(); await Dexie.delete(db.name) }
})

class BrowserUtterance {
  lang = ''
  voice: SpeechSynthesisVoice | null = null
  rate = 1
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(public text: string) {}
}

const installSpeech = (autoStart = true) => {
  const spoken: BrowserUtterance[] = []
  const events = new EventTarget()
  const speech = {
    speaking: false, pending: false, paused: false,
    getVoices: () => [{ lang: 'zh-CN', localService: true, default: true }],
    addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events),
    speak: (utterance: BrowserUtterance) => { spoken.push(utterance); if (autoStart) { speech.speaking = true; utterance.onstart?.() } },
    cancel: vi.fn(() => { speech.speaking = false }),
  }
  vi.stubGlobal('speechSynthesis', speech)
  vi.stubGlobal('SpeechSynthesisUtterance', BrowserUtterance)
  return { spoken, speech, voicesChanged: () => events.dispatchEvent(new Event('voiceschanged')) }
}

const setup = async (options: { secondTip?: boolean; video?: boolean; prepare?: boolean; reps?: boolean; nextAction?: boolean;
  durationSeconds?: number; text?: string; offBeforeMount?: boolean; delayMedia?: boolean } = {}) => {
  const timedSecondTip = options.secondTip ?? false
  const pinia = createPinia()
  setActivePinia(pinia)
  const db = createHachimiDatabase(`synthetic-reminder-audio-${crypto.randomUUID()}`)
  databases.push(db)
  const draft = useDraftStore()
  await draft.load(createDexieDraftRepository(db))
  const candidate: AnalysisCandidate = { id: 'synthetic-squat', name: '深蹲', source_id: 'synthetic-source',
    segment: { start_seconds: 0, end_seconds: 20 },
    parameters: { mode: options.reps ? 'reps' : 'duration', sets: options.reps ? 2 : 1, reps: options.reps ? 12 : null,
      duration_seconds: options.reps ? null : options.durationSeconds ?? 60, rest_seconds: 0 },
    evidence: [], needs_confirmation: false,
    tips: [{ text: options.text ?? (options.prepare ? '开始前把双脚站稳' : '起身时呼气'), category: options.prepare ? 'setup' : 'breathing',
      evidence: { type: 'speech', start_seconds: 5, end_seconds: 8 } },
      ...(timedSecondTip ? [{ text: '脚跟保持贴地', category: 'caution' as const,
        evidence: { type: 'speech' as const, start_seconds: 9, end_seconds: 11 } }] : [])],
  }
  draft.applyCandidateProposal([candidate, ...(options.nextAction
    ? [{ ...candidate, id: 'synthetic-row', name: '俯身划船', tips: [] }] : [])])
  draft.reviewSourceTips(draft.items[0]!.id, timedSecondTip ? [0, 1] : [0])
  const library = useLibraryStore()
  await library.load(createDexieLibraryRepository(db))
  await library.setSpeechReminders(true)
  let releaseMedia: (() => void) | undefined
  const mediaGate = options.delayMedia ? new Promise<void>(resolve => { releaseMedia = resolve }) : null
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (url, request) => {
    if (url === '/api/v1/sources') {
      if (mediaGate) await mediaGate
      return new Response(JSON.stringify(options.video ? [{ id: 'synthetic-source',
        title: '合成参考视频', media_url: '/synthetic-video.mp4', duration_seconds: 20, origin_url: null }] : []))
    }
    const input = JSON.parse(request!.body as string)
    const action = input.actions[0]
    return new Response(JSON.stringify({ plan_id: input.plan_id, plan_revision: input.plan_revision,
      input_fingerprint: await fingerprintTrainingReminderInput(input), contract_version: 'training-reminders.v1.1',
      skill_version: '1.0.0', policy_version: 'ReminderPolicy.v1', mode: 'rules', fallback_reason: 'model_unavailable',
      reminders: [{ action_id: action.action_id, action_revision: action.action_revision, tip_id: action.tips[0].tip_id,
        stage: options.prepare ? 'prepare' : 'training', trigger: options.prepare ? 'action_prepare' : 'set_start',
        set_number: 1, active_seconds: null, priority: 1, max_plays: 1 },
        ...(timedSecondTip ? [{ action_id: action.action_id, action_revision: action.action_revision, tip_id: action.tips[1].tip_id,
          stage: 'training', trigger: options.reps ? 'set_start' : 'active_elapsed', set_number: options.reps ? 2 : 1,
          active_seconds: options.reps ? null : 30, priority: 2, max_plays: 1 }] : [])],
      budgets: [{ action_id: action.action_id, utterance_limit: timedSecondTip ? 2 : 1, active_interval_seconds: 30, end_quiet_seconds: 10 }],
    }))
  }))
  expect(await draft.prepareTrainingReminders(library.preferences.speechReminders!, new AbortController().signal)).toBe(true)
  await draft.flushPersist()
  let elapsed = 0
  let commitGate: Promise<void> | null = null
  const persistence = createDexieTrainingRepository(db)
  const training = useTrainingStore()
  await training.load(createTrainingEngine({ persistence: { ...persistence, commit: async change => {
    if (commitGate) await commitGate
    return persistence.commit(change)
  } },
    clock: { now: () => new Date(1_800_000_000_000 + elapsed), monotonicMilliseconds: () => elapsed },
    estimateCalories: input => { const result = estimateCalories(input); return { value: result.kcal, method: result.method } },
  }))
  expect((await training.createFromDraft(draft.plan, null, library.preferences.speechReminders)).ok).toBe(true)
  if (options.offBeforeMount) await library.setSpeechReminders(false)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/training', component: TrainingView }, { path: '/plan', component: { template: '<div />' } },
  ] })
  await router.push('/training')
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [pinia, router] } })
  wrappers.push(wrapper)
  await vi.waitFor(() => expect(wrapper.text()).toMatch(/自动提醒已就绪|自动提醒已关闭|正在播报动作要点|暂时无法播报，可查看文字要点/))
  return { wrapper, training, library, releaseMedia: () => releaseMedia?.(), holdWrites: () => {
    let release!: () => void
    commitGate = new Promise<void>(resolve => { release = resolve })
    return () => { commitGate = null; release() }
  }, remount: async () => {
    wrapper.unmount()
    const restored = mount({ template: '<RouterView />' }, { global: { plugins: [pinia, router] } })
    wrappers.push(restored)
    await vi.waitFor(() => expect(training.session?.status).toBe('paused'))
    await vi.waitFor(() => expect(restored.text()).toMatch(/自动提醒已就绪|正在播报动作要点/))
    return restored
  }, advance: async (milliseconds: number) => {
    while (milliseconds > 0) {
      const step = Math.min(milliseconds, 1000)
      elapsed += step
      milliseconds -= step
      await training.tick()
    }
  } }
}

const button = (wrapper: VueWrapper, label: string) => wrapper.findAll('button').find(node => node.text() === label)!

it('automatically speaks the reviewed frozen tip at the real set start and keeps its source visible', async () => {
  const { spoken } = installSpeech()
  const { wrapper, training, advance } = await setup()
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  expect(spoken).toHaveLength(0)
  await advance(3000)
  await vi.waitFor(() => expect(spoken.map(utterance => utterance.text)).toEqual(['起身时呼气']))
  expect(spoken[0]!.lang).toBe('zh-CN')
  expect(wrapper.get('.source-tips').text()).toContain('视频语音 · 0:05–0:08')
  expect(training.session?.status).toBe('active')
  expect(training.session?.currentSetActiveMilliseconds).toBe(0)
})

it('spreads different tips over actual active time, without repeating the first after pause and resume', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance } = await setup({ secondTip: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  await vi.waitFor(() => expect(spoken).toHaveLength(1))
  speech.speaking = false
  spoken[0]!.onend?.()
  await advance(10000)
  await button(wrapper, '暂停').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('paused'))
  await button(wrapper, '继续训练').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('active'))
  await advance(19000)
  expect(spoken.map(utterance => utterance.text)).toEqual(['起身时呼气'])
  await advance(1000)
  await vi.waitFor(() => expect(spoken.map(utterance => utterance.text)).toEqual(['起身时呼气', '脚跟保持贴地']))
  expect(wrapper.get('.source-tips > p').text()).toBe('脚跟保持贴地')
  expect(training.session?.currentSetActiveMilliseconds).toBe(30000)
})

it('cancels immediately on mute and skips muted slots rather than replaying them when sound returns', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance } = await setup({ secondTip: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  await vi.waitFor(() => expect(spoken).toHaveLength(1))
  await button(wrapper, '静音').trigger('click')
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  await advance(30000)
  await button(wrapper, '开启声音').trigger('click')
  await advance(1000)
  expect(spoken.map(utterance => utterance.text)).toEqual(['起身时呼气'])
  expect(training.session?.currentSetActiveMilliseconds).toBe(31000)
})

it('ends a stuck utterance and restores text-only operation without pausing the workout', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance } = await setup()
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  await advance(3000)
  await vi.waitFor(() => expect(spoken).toHaveLength(1))
  await vi.advanceTimersByTimeAsync(12000)
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).toContain('声音暂不可用，文字要点仍可查看')
  expect(training.session?.status).toBe('active')
})

it('does not replay a set-start tip after refreshing and restoring at zero elapsed time', async () => {
  const { spoken } = installSpeech()
  const { wrapper, training, advance, remount } = await setup()
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  await vi.waitFor(() => expect(spoken).toHaveLength(1))
  const restored = await remount()
  await button(restored, '继续训练').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('active'))
  expect(spoken.map(utterance => utterance.text)).toEqual(['起身时呼气'])
})

it('stops an unfinished reminder in the final ten seconds of a timed set', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance } = await setup({ secondTip: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  await vi.waitFor(() => expect(spoken).toHaveLength(1))
  speech.speaking = false
  spoken[0]!.onend?.()
  await advance(30000)
  expect(spoken).toHaveLength(2)
  await advance(20000)
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  expect(training.session?.currentSetActiveMilliseconds).toBe(50000)
  expect(training.session?.status).toBe('active')
})

it('ducks the original video while reminding and restores its volume on pause', async () => {
  const { spoken, speech } = installSpeech()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
  const { wrapper, training, advance } = await setup({ video: true })
  const video = wrapper.get<HTMLVideoElement>('video').element
  video.volume = 0.65
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  await vi.waitFor(() => expect(spoken).toHaveLength(1))
  expect(video.volume).toBeCloseTo(0.13)
  await button(wrapper, '暂停').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('paused'))
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  expect(video.volume).toBeCloseTo(0.65)
})

it('uses a preparation tip before the first group and cancels it when the countdown takes priority', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance } = await setup({ prepare: true })
  expect(training.session?.pauseReason).toBe('before_start')
  expect(spoken.map(utterance => utterance.text)).toEqual(['开始前把双脚站稳'])
  expect(wrapper.get('.source-tips > p').text()).toBe('开始前把双脚站稳')
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  await advance(3000)
  expect(spoken).toHaveLength(1)
})

it('leaves practice time between short rep groups instead of filling every group with speech', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance } = await setup({ reps: true, secondTip: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  speech.speaking = false
  spoken[0]!.onend?.()
  await advance(5000)
  await button(wrapper, '完成本组').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(training.session?.currentSetIndex).toBe(1)
  expect(training.session?.status).toBe('active')
  expect(spoken.map(utterance => utterance.text)).toEqual(['起身时呼气'])
})

it('stops speech on the pause gesture even while the training save is still pending', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance, holdWrites } = await setup()
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  const release = holdWrites()
  try {
    await button(wrapper, '暂停').trigger('click')
    expect(speech.cancel).toHaveBeenCalledTimes(1)
    expect(training.session?.status).toBe('active')
  } finally { release() }
  await vi.waitFor(() => expect(training.session?.status).toBe('paused'))
})

it.each(['pagehide', 'visibilitychange'])('cancels before a slow background save on %s and never queues a replay', async event => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance, holdWrites } = await setup()
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  const release = holdWrites()
  try {
    if (event === 'visibilitychange') {
      vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
      document.dispatchEvent(new Event(event))
    } else window.dispatchEvent(new Event(event))
    expect(speech.cancel).toHaveBeenCalledTimes(1)
  } finally { release() }
  await vi.waitFor(() => expect(training.session?.pauseReason).toBe('page_hidden'))
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  await button(wrapper, '继续训练').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('active'))
  expect(spoken).toHaveLength(1)
})

it.each(['完成本组', '提前结束', '跳过剩余组'])('cancels the old reminder at the %s gesture without waiting for storage', async label => {
  const { spoken, speech } = installSpeech()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const { wrapper, training, advance, holdWrites } = await setup({ reps: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  const release = holdWrites()
  try {
    await button(wrapper, label).trigger('click')
    expect(speech.cancel).toHaveBeenCalledTimes(1)
  } finally { release() }
  await vi.waitFor(() => expect(training.session?.status).not.toBe('active'))
})

it('does not carry a previous action voice or displayed tip into the next action', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance } = await setup({ nextAction: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  await training.skipAction()
  await vi.waitFor(() => expect(wrapper.get('h1').text()).toBe('俯身划船'))
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  expect(wrapper.find('.source-tips').exists()).toBe(false)
  expect(training.session?.pauseReason).toBe('between_actions')
})

it('can turn off automatic reminders without muting the reference video and saves that preference', async () => {
  const { spoken, speech } = installSpeech()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
  const { wrapper, training, advance, library } = await setup({ secondTip: true, video: true })
  const video = wrapper.get<HTMLVideoElement>('video').element
  video.volume = 0.7
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  await wrapper.get('button[aria-label="关闭自动提醒"]').trigger('click')
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  expect(video.muted).toBe(false)
  expect(video.volume).toBeCloseTo(0.7)
  await vi.waitFor(() => expect(library.preferencePending).toBe(false))
  await library.reload()
  expect(library.preferences.speechReminders).toEqual({ automatic_speech: false, confirmed: true })
  await advance(30000)
  expect(spoken).toHaveLength(1)
  expect(training.session?.status).toBe('active')
})

it('keeps future tips as text after an audio error rather than retrying speech during the set', async () => {
  const { spoken } = installSpeech()
  const { wrapper, training, advance } = await setup({ secondTip: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  spoken[0]!.onerror?.()
  await advance(30000)
  expect(spoken).toHaveLength(1)
  expect(wrapper.get('.source-tips > p').text()).toBe('脚跟保持贴地')
  expect(wrapper.text()).toContain('声音暂不可用，文字要点仍可查看')
  expect(training.session?.status).toBe('active')
})

it('keeps a long verified sentence intact as text when it cannot fit before the short-set quiet period', async () => {
  const { spoken } = installSpeech()
  const text = '用脚跟稳定撑住地面，起身时缓缓呼气，同时保持躯干平稳，不需要赶速度，按照视频动作路径控制下蹲和起身。'
  const { wrapper, training, advance } = await setup({ durationSeconds: 15, text })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(0)
  expect(wrapper.get('.source-tips > p').text()).toBe(text)
  expect(training.session?.status).toBe('active')
  expect(training.session?.plan.items[0]?.sourceTips?.[0]?.text).toBe(text)
})

it('does not describe a merely queued utterance as playing, and ignores a late start after mute', async () => {
  const { spoken } = installSpeech(false)
  const { wrapper, training, advance } = await setup()
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  expect(wrapper.text()).not.toContain('正在播报动作要点')
  const lateStart = spoken[0]!.onstart
  await button(wrapper, '静音').trigger('click')
  lateStart?.()
  expect(wrapper.text()).toContain('自动提醒已关闭，文字要点仍可查看')
  expect(training.session?.status).toBe('active')
})

it('uses a later available local voice only for a future slot, without backfilling the missed start tip', async () => {
  const { spoken, speech, voicesChanged } = installSpeech()
  speech.getVoices = () => []
  const { wrapper, training, advance } = await setup({ secondTip: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(0)
  speech.getVoices = () => [{ lang: 'zh-CN', localService: true, default: true }]
  voicesChanged()
  expect(spoken).toHaveLength(0)
  await advance(30000)
  expect(spoken.map(utterance => utterance.text)).toEqual(['脚跟保持贴地'])
})

it('does not replay an already handled preparation reminder when refreshing before the first set', async () => {
  const { spoken } = installSpeech()
  const { training, remount } = await setup({ prepare: true })
  expect(spoken.map(utterance => utterance.text)).toEqual(['开始前把双脚站稳'])
  await remount()
  expect(training.session?.pauseReason).toBe('before_start')
  expect(spoken).toHaveLength(1)
})

it('cancels a voice that begins too late instead of speaking a stale set-start reminder', async () => {
  const { spoken, speech } = installSpeech(false)
  const { wrapper, training, advance } = await setup()
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  await advance(10000)
  spoken[0]!.onstart?.()
  expect(speech.cancel).toHaveBeenCalledTimes(1)
  expect(training.session?.status).toBe('active')
  expect(wrapper.text()).not.toContain('正在播报动作要点')
})

it('does not backfill a preparation slot that was already encountered with reminders disabled', async () => {
  const { spoken } = installSpeech()
  const { library } = await setup({ prepare: true, offBeforeMount: true })
  expect(spoken).toHaveLength(0)
  await library.setSpeechReminders(true)
  expect(spoken).toHaveLength(0)
})

it('restores legitimate fractional-millisecond speech progress without disabling later reminders', async () => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, advance, remount } = await setup({ secondTip: true })
  await button(wrapper, '准备好了').trigger('click')
  await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
  await advance(3000)
  expect(spoken).toHaveLength(1)
  speech.speaking = false
  spoken[0]!.onend?.()
  await advance(0.5)
  await advance(30000)
  expect(spoken).toHaveLength(2)
  expect(training.session?.currentSetActiveMilliseconds).toBe(30000.5)
  const restored = await remount()
  expect(restored.text()).toContain('自动提醒已就绪')
  expect(spoken).toHaveLength(2)
})

it.each(['pagehide', 'visibilitychange'])('cancels a preparation voice on %s while source loading is still pending', async event => {
  const { spoken, speech } = installSpeech()
  const { wrapper, training, releaseMedia } = await setup({ prepare: true, video: true, delayMedia: true })
  try {
    expect(spoken.map(utterance => utterance.text)).toEqual(['开始前把双脚站稳'])
    if (event === 'visibilitychange') {
      vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
      document.dispatchEvent(new Event(event))
    } else window.dispatchEvent(new Event(event))
    expect(speech.cancel).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(wrapper.text()).not.toContain('正在播报动作要点'))
    expect(training.session?.status).toBe('paused')
  } finally { releaseMedia() }
})
