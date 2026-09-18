import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { DraftPlan, Segment } from '@/domain/types'
import type { CoachStyleId } from '@/domain/coach'
import type {
  PlanSnapshot,
  TrainingRecord,
  TrainingSession,
} from '@/domain/training'
import { QUICK_EXPERIENCE_PLAN_NAME } from '@/features/quick-experience/fixture'
import type {
  PlanValidationIssue,
  TrainingEngine,
  TrainingEngineErrorCode,
  TrainingEngineResult,
} from '@/training/training-engine'

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

export const useTrainingStore = defineStore('training', () => {
  const session = ref<TrainingSession | null>(null)
  const lastRecord = ref<TrainingRecord | null>(null)
  const errorCode = ref<TrainingEngineErrorCode | null>(null)
  const errorMessage = ref<string | null>(null)
  const validationIssues = ref<PlanValidationIssue[]>([])
  const loaded = ref(false)
  const conflictLocked = ref(false)
  const persistenceSuspended = ref(false)
  let engine: TrainingEngine | null = null
  let commandQueue: Promise<void> = Promise.resolve()

  const serialize = <T>(operation: () => Promise<T>): Promise<T> => {
    const result = commandQueue.then(operation, operation)
    commandQueue = result.then(() => undefined, () => undefined)
    return result
  }

  const hasCurrent = computed(() => session.value !== null)
  const currentItem = computed(() =>
    session.value?.plan.items[session.value.currentItemIndex] ?? null,
  )
  const currentProgress = computed(() =>
    session.value?.progress[session.value.currentItemIndex] ?? null,
  )
  const commandLocked = computed(() =>
    conflictLocked.value || errorCode.value === 'storage_unavailable',
  )

  const applyResult = (result: TrainingEngineResult): TrainingEngineResult => {
    if (result.session) lastRecord.value = null
    if (result.ok) {
      session.value = result.session
      if (result.record) lastRecord.value = result.record
      errorCode.value = null
      errorMessage.value = null
      validationIssues.value = []
      conflictLocked.value = false
      return result
    }

    if (result.session) {
      session.value = result.code === 'session_conflict' && ['active', 'countdown', 'resting'].includes(result.session.status)
        ? {
            ...result.session,
            status: 'paused',
            pauseReason: 'recovered',
            activeStartedAt: null,
          }
        : result.session
    }
    if (
      result.code === 'storage_unavailable'
      && session.value && ['active', 'countdown', 'resting'].includes(session.value.status)
    ) {
      session.value = {
        ...session.value,
        status: 'paused',
        pauseReason: 'recovered',
        activeStartedAt: null,
      }
    }
    errorCode.value = result.code
    errorMessage.value = result.code === 'session_conflict'
      ? '训练状态已在其他页面更新，此页面已暂停'
      : result.code === 'storage_unavailable'
        ? '本机训练数据暂时无法写入，此页面已暂停'
        : result.message
    validationIssues.value = result.issues ?? []
    if (result.code === 'session_conflict') conflictLocked.value = true
    return result
  }

  const unavailable = (): TrainingEngineResult => ({
    ok: false,
    code: 'storage_unavailable',
    message: '本机训练数据暂时无法读取',
    session: session.value,
  })

  async function load(nextEngine: TrainingEngine): Promise<TrainingEngineResult> {
    engine = nextEngine
    const result = applyResult(await engine.restore())
    if (!result.ok && result.code === 'storage_unavailable') {
      loaded.value = false
      throw new Error(result.message)
    }
    loaded.value = true
    return result
  }

  async function restore(): Promise<TrainingEngineResult> {
    return serialize(async () => {
      if (!engine) return applyResult(unavailable())
      return applyResult(await engine.restore())
    })
  }

  async function createFromDraft(
    draft: DraftPlan,
    coachStyleId: CoachStyleId | null = null,
  ): Promise<TrainingEngineResult> {
    return serialize(async () => {
      if (persistenceSuspended.value) return applyResult(unavailable())
      if (!engine) return applyResult(unavailable())
      const snapshot: PlanSnapshot = {
        name: draft.name,
        source: draft.linkedPlanId
          ? 'saved'
          : draft.name === QUICK_EXPERIENCE_PLAN_NAME ? 'sample' : 'draft',
        sourcePlanId: draft.linkedPlanId,
        items: cloneJson(draft.items),
      }
      const result = await engine.dispatch({
        type: 'session.create',
        plan: snapshot,
        coachStyleId,
        flowVersion: 'watch-v1',
      })
      return applyResult(result)
    })
  }

  async function runVersioned(
    command: (
      current: TrainingSession,
    ) => Parameters<TrainingEngine['dispatch']>[0],
  ): Promise<TrainingEngineResult> {
    return serialize(async () => {
      if (persistenceSuspended.value) return applyResult(unavailable())
      if (commandLocked.value) {
        const storageFailure = errorCode.value === 'storage_unavailable'
        return applyResult({
          ok: false,
          code: storageFailure ? 'storage_unavailable' : 'session_conflict',
          message: storageFailure
            ? '本机训练数据暂时无法写入，此页面已暂停'
            : '训练状态已在其他页面更新，此页面已暂停',
          session: session.value,
        })
      }
      if (!engine || !session.value) {
        return applyResult({
          ok: false,
          code: 'no_active_session',
          message: '没有可继续的训练',
          session: null,
        })
      }
      return applyResult(await engine.dispatch(command(session.value)))
    })
  }

  const startSet = () => runVersioned((current) => ({
    type: 'set.start',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const prepareSet = () => runVersioned((current) => ({
    type: 'set.prepare', sessionId: current.sessionId, expectedRevision: current.revision,
  }))

  const selectPlayback = (input: { sessionId: string; expectedRevision: number; itemId: string; fingerprint: string; range: Segment | null }) =>
    runVersioned(() => ({ type: 'playback.select', ...input }))

  const tick = () => runVersioned((current) => ({
    type: 'clock.tick',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const pause = (reason: 'user' | 'page_hidden') => runVersioned((current) => ({
    type: 'session.pause',
    reason,
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  // Inspect state only after earlier commands settle: a pending preparation or
  // resume may still look paused at the moment the page becomes hidden.
  const pauseForLeave = () => serialize(async () => {
    const current = session.value
    if (!engine || !current || persistenceSuspended.value || commandLocked.value
      || !['active', 'countdown', 'resting'].includes(current.status)) return
    applyResult(await engine.dispatch({ type: 'session.pause', reason: 'page_hidden',
      sessionId: current.sessionId, expectedRevision: current.revision }))
  })

  const completeSet = () => runVersioned((current) => ({
    type: 'set.complete',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const continueRest = () => runVersioned((current) => ({
    type: 'rest.continue',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const skipAction = () => runVersioned((current) => ({
    type: 'action.skip',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  const endEarly = () => runVersioned((current) => ({
    type: 'session.end_early',
    sessionId: current.sessionId,
    expectedRevision: current.revision,
  }))

  async function quiescePersistence(): Promise<void> {
    persistenceSuspended.value = true
    await commandQueue
  }

  function resumePersistence(): void {
    persistenceSuspended.value = false
  }

  function resetLocalState(keepSuspended = false): void {
    commandQueue = Promise.resolve()
    session.value = null
    lastRecord.value = null
    errorCode.value = null
    errorMessage.value = null
    validationIssues.value = []
    conflictLocked.value = false
    persistenceSuspended.value = keepSuspended
  }

  return {
    session,
    lastRecord,
    errorCode,
    errorMessage,
    validationIssues,
    loaded,
    conflictLocked,
    persistenceSuspended,
    hasCurrent,
    currentItem,
    currentProgress,
    commandLocked,
    load,
    restore,
    createFromDraft,
    startSet,
    prepareSet,
    selectPlayback,
    tick,
    pause,
    pauseForLeave,
    completeSet,
    continueRest,
    skipAction,
    endEarly,
    quiescePersistence,
    resumePersistence,
    resetLocalState,
  }
})
