import 'fake-indexeddb/auto'

import { mount, type VueWrapper } from '@vue/test-utils'
import Dexie from 'dexie'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { createDexieDraftRepository } from '@/db/draft-repository'
import { createHachimiDatabase } from '@/db/hachimi-database'
import { createDexieLibraryRepository } from '@/db/library-repository'
import { createDexieTrainingRepository } from '@/db/training-repository'
import type { TrainingSession } from '@/domain/training'
import type { AnalysisCandidate, DraftItem, SourceTip } from '@/domain/types'
import { estimateCalories } from '@/features/experience/calorie'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'
import { createTrainingEngine, type TrainingEngineResult } from '@/training/training-engine'
import PlanDraftView from '@/views/PlanDraftView.vue'
import ResultView from '@/views/ResultView.vue'
import TrainingView from '@/views/TrainingView.vue'

const databases: string[] = []
afterEach(async () => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  await Promise.all(databases.splice(0).map(name => Dexie.delete(name)))
})

const success = (result: TrainingEngineResult) => {
  expect(result.ok).toBe(true)
  if (!result.ok) throw new Error(result.code)
  return result
}

it.each([3, 5])('preserves %i video tips through editing, archive, refresh, record and repractice', async (count) => {
  const name = `trainpal-tip-roundtrip-${crypto.randomUUID()}`
  databases.push(name)
  let database = createHachimiDatabase(name)
  setActivePinia(createPinia())
  let store = useDraftStore()
  await store.load(createDexieDraftRepository(database))
  const tips: SourceTip[] = [
    { text: '先站稳', category: 'setup', evidence: { type: 'visual', start_seconds: 0, end_seconds: 1 } },
    { text: '缓慢下蹲', category: 'path', evidence: { type: 'visual', start_seconds: 1, end_seconds: 2 } },
    { text: '保持呼吸', category: 'breathing', evidence: { type: 'visual', start_seconds: 2, end_seconds: 3 } },
    { text: '不要突然加速', category: 'caution', evidence: { type: 'visual', start_seconds: 3, end_seconds: 4 } },
    { text: '双脚保持稳定', category: 'path', evidence: { type: 'visual', start_seconds: 4, end_seconds: 5 } },
  ].slice(0, count) as SourceTip[]
  const candidate: AnalysisCandidate = {
    id: 'candidate', name: '合成深蹲', source_id: 'source',
    segment: { start_seconds: 0, end_seconds: 10 },
    parameters: { mode: 'reps', sets: 1, reps: 1, duration_seconds: null, rest_seconds: 0 },
    evidence: [{ type: 'visual', start_seconds: 0, end_seconds: 10 }],
    needs_confirmation: false, tips,
  }
  store.applyCandidateProposal([candidate])
  store.updateValue(store.items[0]!.id, 'reps', 7)
  await store.flushPersist()
  const saved = await createDexieLibraryRepository(database).saveCurrentDraftAs('来源要点方案')
  expect(saved.plan.items[0]?.sourceTips).toEqual(tips)
  database.close()

  // Reopen the database and stores, rather than inspecting their private snapshots.
  database = createHachimiDatabase(name)
  setActivePinia(createPinia())
  store = useDraftStore()
  await store.load(createDexieDraftRepository(database))
  expect(store.items[0]?.sourceTips).toEqual(tips)
  expect(store.items[0]?.reps).toEqual({ value: 7, source: 'user' })
  const library = createDexieLibraryRepository(database)
  const opened = await library.openPlan(saved.plan.id)
  expect(opened.items[0]?.sourceTips).toEqual(tips)
  const persistence = createDexieTrainingRepository(database)
  let elapsed = 0
  let sequence = 0
  const engine = createTrainingEngine({
    persistence,
    clock: { now: () => new Date(1_800_000_000_000 + elapsed), monotonicMilliseconds: () => elapsed },
    idFactory: () => `session-${++sequence}`,
    estimateCalories: () => ({ value: 1, method: 'generic' }),
  })
  const created = success(await engine.dispatch({
    type: 'session.create', coachStyleId: null, flowVersion: 'watch-v1',
    plan: { name: opened.name, source: 'saved', sourcePlanId: saved.plan.id, items: opened.items },
  }))
  expect(created.session?.plan.items[0]?.sourceTips).toEqual(tips)
  const started = success(await engine.dispatch({
    type: 'set.start', sessionId: created.session!.sessionId, expectedRevision: created.session!.revision,
  }))
  elapsed += 1_000
  const completed = success(await engine.dispatch({
    type: 'set.complete', sessionId: started.session!.sessionId, expectedRevision: started.session!.revision,
  }))
  expect(completed.record?.plan.items[0]?.sourceTips).toEqual(tips)
  const records = await library.listRecords()
  expect(records[0]?.plan.items[0]?.sourceTips).toEqual(tips)
  const repractice = success(await engine.dispatch({
    type: 'session.create', coachStyleId: null, flowVersion: 'watch-v1', plan: records[0]!.plan,
  }))
  expect(repractice.session?.plan.items[0]?.sourceTips).toEqual(tips)
  expect(repractice.session?.plan.items[0]?.reps).toEqual({ value: 7, source: 'user' })
  database.close()
})

it('continues a historical unfinished session with its three unreviewed tips, manual values and one-shot playback, then repractices its record', async () => {
  const name = `trainpal-legacy-tip-roundtrip-${crypto.randomUUID()}`
  databases.push(name)
  // Explicit synthetic fixture matching the v5 storage/types at 1356a32.
  // An absent flowVersion retains pre-watch-v1 playback; no review or speech fields existed.
  const legacyItem: DraftItem = {
    id: 'synthetic-legacy-squat', name: '合成旧深蹲',
    sourceRef: { sourceId: 'synthetic-legacy-source', kind: 'controlled', title: '合成旧教学来源' },
    sourceParameters: { mode: 'reps', sets: 3, reps: 10, duration_seconds: null, rest_seconds: 30 },
    sourceEvidence: [
      { type: 'speech', start_seconds: 10, end_seconds: 12 },
      { type: 'visual', start_seconds: 14, end_seconds: 16 },
    ],
    segment: { value: { start_seconds: 10, end_seconds: 30 }, source: 'video' },
    playbackOptions: [{ label: '合成动作示范', start_seconds: 12, end_seconds: 18 }],
    playbackSelection: { start_seconds: 12, end_seconds: 18 },
    sourceTips: [
      { text: '开始前先把双脚站稳，保持脚掌贴地。', category: 'setup',
        evidence: { type: 'speech', start_seconds: 10, end_seconds: 12 } },
      { text: '缓慢向下坐，膝盖沿着脚尖方向移动。', category: 'path',
        evidence: { type: 'visual', start_seconds: 14, end_seconds: 16 } },
      { text: '起身时呼气，不要为了赶次数突然加速。', category: 'breathing',
        evidence: { type: 'speech', start_seconds: 18, end_seconds: 21 } },
    ],
    mode: 'reps', sets: { value: 2, source: 'user' }, reps: { value: 7, source: 'user' },
    durationSeconds: { value: null, source: null }, restSeconds: { value: 8, source: 'user' },
    weightKg: { value: 4, source: 'user' },
  }
  const legacySession: TrainingSession = {
    id: 'current', sessionId: 'synthetic-legacy-session', revision: 4,
    status: 'active', pauseReason: null,
    plan: { name: '合成旧三要点方案', source: 'saved', sourcePlanId: 'synthetic-legacy-plan', items: [legacyItem] },
    currentItemIndex: 0, currentSetIndex: 1, currentSetActiveMilliseconds: 4_000,
    activeStartedAt: '2026-09-01T00:00:23.000Z', restStartedAt: null, restEndsAt: null,
    scheduledRestSeconds: null, creditedRestMilliseconds: 8_000,
    progress: [{ itemId: legacyItem.id, completedSets: 1, activeMilliseconds: 15_000, skipped: false }],
    petId: 'hachimi', coachStyleId: null,
    startedAt: '2026-09-01T00:00:00.000Z', updatedAt: '2026-09-01T00:00:27.000Z',
  }
  const legacyDatabase = new Dexie(name)
  legacyDatabase.version(5).stores({
    drafts: '&id, updatedAt, linkedPlanId', plans: '&id, updatedAt, createdAt, name',
    sessions: '&id, sessionId, status, updatedAt', records: '&id, endedAt, outcome',
    profiles: '&id, updatedAt', preferences: '&id, updatedAt', localMedia: '&sourceId, importedAt, updatedAt',
    gymtiAttempts: '&id, attemptId, updatedAt', gymtiPendingResults: '&id, resultId, updatedAt',
    gymtiResults: '&id, resultId, updatedAt',
  })
  await legacyDatabase.table('sessions').put(legacySession)
  await legacyDatabase.table('plans').put({
    id: 'synthetic-legacy-plan', name: legacySession.plan.name, items: [legacyItem],
    createdAt: legacySession.startedAt, updatedAt: legacySession.updatedAt,
  })
  await legacyDatabase.table('preferences').put({
    id: 'current', petVisible: true, coachStyleId: null, updatedAt: legacySession.updatedAt,
  })
  legacyDatabase.close()

  const database = createHachimiDatabase(name)
  let wrapper: VueWrapper | undefined
  try {
    const fetcher = vi.fn<typeof fetch>(async (url) => {
      if (url !== '/api/v1/sources') throw new Error('unexpected HTTP request in synthetic legacy recovery')
      return new Response(JSON.stringify([{ id: 'synthetic-legacy-source', title: '合成旧教学来源',
        media_url: '/synthetic-legacy-video.mp4', duration_seconds: 40, origin_url: null }]))
    })
    vi.stubGlobal('fetch', fetcher)
    class SyntheticBrowserUtterance {
      lang = ''
      voice: SpeechSynthesisVoice | null = null
      rate = 1
      onstart: (() => void) | null = null
      onend: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor(public text: string) {}
    }
    const voices = [{ name: 'Synthetic local Chinese', voiceURI: 'synthetic-local-zh',
      lang: 'zh-CN', localService: true, default: true }]
    const speechEvents = new EventTarget()
    const speak = vi.fn((utterance: SyntheticBrowserUtterance) => {
      utterance.onstart?.()
      utterance.onend?.()
    })
    vi.stubGlobal('SpeechSynthesisUtterance', SyntheticBrowserUtterance)
    vi.stubGlobal('speechSynthesis', {
      speaking: false, pending: false, paused: false, getVoices: () => voices, speak, cancel: vi.fn(),
      addEventListener: speechEvents.addEventListener.bind(speechEvents),
      removeEventListener: speechEvents.removeEventListener.bind(speechEvents),
    })
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const pinia = createPinia()
    setActivePinia(pinia)
    const draft = useDraftStore()
    const library = useLibraryStore()
    const training = useTrainingStore()
    await draft.load(createDexieDraftRepository(database))
    await library.load(createDexieLibraryRepository(database))
    let elapsed = 0
    success(await training.load(createTrainingEngine({
      persistence: createDexieTrainingRepository(database),
      clock: { now: () => new Date(Date.parse('2026-10-07T00:00:00.000Z') + elapsed), monotonicMilliseconds: () => elapsed },
      idFactory: () => 'synthetic-repractice-session',
      estimateCalories: input => { const result = estimateCalories(input); return { value: result.kcal, method: result.method } },
    })))
    expect(training.session).toMatchObject({
      sessionId: 'synthetic-legacy-session', status: 'paused', pauseReason: 'recovered',
      currentSetIndex: 1, currentSetActiveMilliseconds: 4_000,
      creditedRestMilliseconds: 8_000, progress: legacySession.progress,
    })
    expect(training.currentItem).toEqual(legacyItem)
    expect(training.session?.flowVersion).toBeUndefined()
    expect(training.session?.plan.reminderCache).toBeUndefined()
    expect(training.currentItem?.reviewedSourceTips).toBeUndefined()
    expect(library.preferences.speechReminders).toBeUndefined()
    expect(library.records).toEqual([])

    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/training', component: TrainingView }, { path: '/plan', component: PlanDraftView },
      { path: '/result/:recordId', component: ResultView },
    ] })
    await router.push('/training')
    await router.isReady()
    wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [pinia, router] } })
    const button = (label: string) => wrapper!.findAll('button').find(node => node.text() === label)!
    await vi.waitFor(() => expect(wrapper!.find('video').exists()).toBe(true))
    await wrapper.get('video').trigger('loadedmetadata')
    expect(wrapper.get('video').element.currentTime).toBe(12)
    await button('继续训练').trigger('click')
    await vi.waitFor(() => expect(training.session?.status).toBe('active'))
    expect(wrapper.get('.set-counter').text()).toContain('2/ 2 组')
    expect(wrapper.findAll('.source-tips li').map(tip => tip.text())).toEqual([
      '开始前先把双脚站稳，保持脚掌贴地。视频语音 · 0:10–0:12',
      '缓慢向下坐，膝盖沿着脚尖方向移动。画面字幕 · 0:14–0:16',
      '起身时呼气，不要为了赶次数突然加速。视频语音 · 0:18–0:21',
    ])
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    play.mockClear()
    pause.mockClear()
    wrapper.get('video').element.currentTime = 18
    await wrapper.get('video').trigger('timeupdate')
    await wrapper.get('video').trigger('ended')
    expect(wrapper.get('video').element.currentTime).toBe(18)
    expect(pause).toHaveBeenCalled()
    expect(play).not.toHaveBeenCalled()
    expect(training.session?.status).toBe('active')
    expect(training.currentProgress?.completedSets).toBe(1)

    elapsed += 6_000
    await button('完成本组').trigger('click')
    await vi.waitFor(() => expect(training.lastRecord?.outcome).toBe('completed'))
    expect(training.lastRecord).toMatchObject({
      id: 'synthetic-legacy-session', activeSeconds: 25, creditedRestSeconds: 8,
      trainingDurationSeconds: 33, completedActionCount: 1,
      actions: [{ itemId: legacyItem.id, targetSets: 2, completedSets: 2, completedReps: 14,
        completedDurationSeconds: null, activeSeconds: 25, status: 'completed' }],
    })
    expect(training.lastRecord?.plan.items).toEqual([legacyItem])
    expect(training.lastRecord?.plan.reminderCache).toBeUndefined()
    expect(speak).not.toHaveBeenCalled()
    await library.refreshHistory()
    expect(library.records).toEqual([training.lastRecord])
    const record = JSON.parse(JSON.stringify(library.records[0]))
    await router.push('/result/synthetic-legacy-session')
    await button('再练一次').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/plan'))
    expect(draft.items[0]).toMatchObject(legacyItem)
    expect(draft.items[0]?.sourceTips).toEqual(legacyItem.sourceTips)
    expect(draft.items[0]?.reviewedSourceTips).toBeUndefined()
    expect(draft.plan.reminderCache).toBeUndefined()
    expect(wrapper.get<HTMLInputElement>('input[aria-label="训练时听动作要点"]').element.checked).toBe(false)
    await button('开始训练').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/training'))
    expect(training.session?.sessionId).toBe('synthetic-repractice-session')
    expect(training.session?.plan.items[0]).toMatchObject(legacyItem)
    expect(training.session?.plan.items[0]?.reviewedSourceTips).toBeUndefined()
    expect(training.session?.plan.reminderCache).toBeUndefined()
    expect(training.currentProgress?.completedSets).toBe(0)
    await button('准备好了').trigger('click')
    await vi.waitFor(() => expect(training.session?.status).toBe('countdown'))
    elapsed += 3_000
    success(await training.tick())
    expect(training.session?.status).toBe('active')
    expect(wrapper.findAll('.source-tips li')).toHaveLength(3)
    await library.refreshHistory()
    expect(library.records).toEqual([record])
    expect(library.plans.find(plan => plan.id === 'synthetic-legacy-plan')?.items).toEqual([legacyItem])
    expect(library.preferences.speechReminders).toBeUndefined()
    expect(confirm).not.toHaveBeenCalled()
    expect(speak).not.toHaveBeenCalled()
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/v1/sources'])
  } finally {
    wrapper?.unmount()
    database.close()
  }
})
