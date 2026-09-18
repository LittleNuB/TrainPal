import type { TrainingPersistence } from '@/db/training-repository'
import type { DraftItem, Segment } from '@/domain/types'
import { playbackFingerprint, validSelection } from '@/domain/playback'
import type { CoachStyleId } from '@/domain/coach'
import type {
  ActionProgress,
  ActionResult,
  CalorieEstimate,
  PlanSnapshot,
  TrainingEvent,
  TrainingProfile,
  TrainingRecord,
  TrainingSession,
} from '@/domain/training'

export interface TrainingClock {
  now(): Date
  monotonicMilliseconds(): number
}

export interface CalorieEstimatorInput {
  profile: TrainingProfile | null
  activeMilliseconds: number
  creditedRestMilliseconds: number
}

export type CalorieEstimator = (input: CalorieEstimatorInput) => CalorieEstimate

type VersionedCommand = {
  sessionId: string
  expectedRevision: number
}

export type TrainingCommand =
  | { type: 'session.create'; plan: PlanSnapshot; coachStyleId: CoachStyleId | null; flowVersion?: 'watch-v1' }
  | ({ type: 'set.prepare' } & VersionedCommand)
  | ({ type: 'playback.select'; itemId: string; fingerprint: string; range: Segment | null } & VersionedCommand)
  | ({ type: 'set.start' } & VersionedCommand)
  | ({ type: 'clock.tick' } & VersionedCommand)
  | ({ type: 'session.pause'; reason: 'user' | 'page_hidden' } & VersionedCommand)
  | ({ type: 'set.complete' } & VersionedCommand)
  | ({ type: 'rest.continue' } & VersionedCommand)
  | ({ type: 'action.skip' } & VersionedCommand)
  | ({ type: 'session.end_early' } & VersionedCommand)

export interface PlanValidationIssue {
  itemId: string | null
  field: string
  message: string
}

export type TrainingEngineErrorCode =
  | 'invalid_plan'
  | 'active_session_exists'
  | 'no_active_session'
  | 'session_conflict'
  | 'invalid_transition'
  | 'end_confirmation_required'
  | 'storage_unavailable'

export type TrainingEngineResult =
  | {
      ok: true
      session: TrainingSession | null
      record: TrainingRecord | null
      events: TrainingEvent[]
    }
  | {
      ok: false
      code: TrainingEngineErrorCode
      message: string
      session: TrainingSession | null
      issues?: PlanValidationIssue[]
    }

export interface TrainingEngine {
  restore(): Promise<TrainingEngineResult>
  dispatch(command: TrainingCommand): Promise<TrainingEngineResult>
}

interface TrainingEngineDependencies {
  persistence: TrainingPersistence
  clock?: TrainingClock
  idFactory?: () => string
  estimateCalories: CalorieEstimator
}

const browserClock: TrainingClock = {
  now: () => new Date(),
  monotonicMilliseconds: () => globalThis.performance.now(),
}

const clone = <T>(value: T): T => structuredClone(value)

const success = (
  session: TrainingSession | null,
  record: TrainingRecord | null = null,
  events: TrainingEvent[] = [],
): TrainingEngineResult => ({ ok: true, session, record, events })

const failure = (
  code: TrainingEngineErrorCode,
  message: string,
  session: TrainingSession | null = null,
  issues?: PlanValidationIssue[],
): TrainingEngineResult => ({ ok: false, code, message, session, issues })

const positiveInteger = (value: number | null): value is number =>
  Number.isInteger(value) && value !== null && value > 0

const nonNegativeInteger = (value: number | null): value is number =>
  Number.isInteger(value) && value !== null && value >= 0

const validatePlan = (plan: PlanSnapshot): PlanValidationIssue[] => {
  const issues: PlanValidationIssue[] = []
  const ids = new Set<string>()

  if (!plan.items.length) {
    issues.push({ itemId: null, field: 'items', message: '至少需要一个训练动作' })
  }

  for (const item of plan.items) {
    if (!item.id || ids.has(item.id)) {
      issues.push({ itemId: item.id || null, field: 'id', message: '动作标识必须唯一' })
    }
    ids.add(item.id)
    if (!item.name.trim()) {
      issues.push({ itemId: item.id, field: 'name', message: '动作名称不能为空' })
    }
    if (!positiveInteger(item.sets.value)) {
      issues.push({ itemId: item.id, field: 'sets', message: '组数必须是正整数' })
    }
    if (item.mode === 'reps') {
      if (!positiveInteger(item.reps.value) || item.durationSeconds.value !== null) {
        issues.push({ itemId: item.id, field: 'reps', message: '次数型动作需要正整数次数' })
      }
    } else if (!positiveInteger(item.durationSeconds.value) || item.reps.value !== null) {
      issues.push({ itemId: item.id, field: 'durationSeconds', message: '时长型动作需要正整数秒数' })
    }
    if (!nonNegativeInteger(item.restSeconds.value)) {
      issues.push({ itemId: item.id, field: 'restSeconds', message: '休息时间必须是非负整数' })
    }
    if (
      item.weightKg.value !== null
      && (!(item.weightKg.value > 0) || item.weightKg.source !== 'user')
    ) {
      issues.push({ itemId: item.id, field: 'weightKg', message: '重量只能是用户填写的正数' })
    }

    const hasSource = item.sourceRef !== null
    const hasSegment = item.segment.value !== null
    if (hasSource !== hasSegment) {
      issues.push({ itemId: item.id, field: 'segment', message: '视频动作需要同时保留来源和演示片段' })
    } else if (
      item.segment.value
      && (
        !Number.isFinite(item.segment.value.start_seconds)
        || !Number.isFinite(item.segment.value.end_seconds)
        || item.segment.value.start_seconds < 0
        || item.segment.value.end_seconds <= item.segment.value.start_seconds
      )
    ) {
      issues.push({ itemId: item.id, field: 'segment', message: '演示片段时间无效' })
    }
  }

  return issues
}

const activeMilliseconds = (session: TrainingSession): number =>
  session.progress.reduce((total, action) => total + action.activeMilliseconds, 0)
    + session.currentSetActiveMilliseconds

const actionResult = (item: DraftItem, progress: ActionProgress): ActionResult => {
  const targetSets = item.sets.value ?? 0
  const status = progress.completedSets >= targetSets
    ? 'completed'
    : progress.completedSets > 0
      ? 'partial'
      : 'skipped'

  return {
    itemId: item.id,
    name: item.name,
    targetSets,
    completedSets: progress.completedSets,
    completedReps: item.mode === 'reps'
      ? (item.reps.value ?? 0) * progress.completedSets
      : null,
    completedDurationSeconds: item.mode === 'duration'
      ? (item.durationSeconds.value ?? 0) * progress.completedSets
      : null,
    activeSeconds: Math.round(progress.activeMilliseconds / 1_000),
    status,
  }
}

export const createTrainingEngine = ({
  persistence,
  clock = browserClock,
  idFactory = () => globalThis.crypto.randomUUID(),
  estimateCalories,
}: TrainingEngineDependencies): TrainingEngine => {
  let activeCheckpoint: { sessionId: string; milliseconds: number } | null = null

  const prepare = (session: TrainingSession, now: Date): TrainingSession => ({
    ...session, status: 'countdown', pauseReason: null, pendingCountdown: true, autoAdvanceSuspended: false,
    countdownEndsAt: new Date(now.getTime() + 3_000).toISOString(),
    activeStartedAt: null,
  })

  const afterRest = (session: TrainingSession, now: Date): TrainingSession => {
    if (session.flowVersion !== 'watch-v1') return session
    if (session.autoAdvanceSuspended) return session
    return session.currentSetIndex === 0
      ? { ...session, status: 'paused', pauseReason: 'between_actions' }
      : prepare(session, now)
  }

  const withRevision = (session: TrainingSession, now: Date): TrainingSession => ({
    ...session,
    revision: session.revision + 1,
    updatedAt: now.toISOString(),
  })

  const flushActive = (session: TrainingSession): TrainingSession => {
    if (session.status !== 'active' || activeCheckpoint?.sessionId !== session.sessionId) {
      return session
    }
    const current = clock.monotonicMilliseconds()
    const elapsed = Math.max(0, current - activeCheckpoint.milliseconds)
    activeCheckpoint = { sessionId: session.sessionId, milliseconds: current }
    return {
      ...session,
      currentSetActiveMilliseconds: session.currentSetActiveMilliseconds + elapsed,
    }
  }

  const settleRest = (
    session: TrainingSession,
    now: Date,
    nextStatus: 'ready_to_continue' | 'active',
  ): TrainingSession => {
    const startedAt = session.restStartedAt ? Date.parse(session.restStartedAt) : now.getTime()
    const scheduledMilliseconds = (session.scheduledRestSeconds ?? 0) * 1_000
    const elapsedMilliseconds = Math.max(0, now.getTime() - startedAt)
    return {
      ...session,
      status: nextStatus,
      pauseReason: null,
      creditedRestMilliseconds: session.creditedRestMilliseconds
        + Math.min(elapsedMilliseconds, scheduledMilliseconds),
      activeStartedAt: nextStatus === 'active' ? now.toISOString() : null,
      restStartedAt: null,
      restEndsAt: null,
      scheduledRestSeconds: null,
    }
  }

  const commitSession = async (
    previous: TrainingSession,
    next: TrainingSession,
    events: TrainingEvent[],
    persistPlayback = false,
  ): Promise<TrainingEngineResult> => {
    try {
      const result = await persistence.commit({
        sessionId: previous.sessionId,
        expectedRevision: previous.revision,
        nextSession: next,
        persistPlayback,
      })
      if (result.status === 'committed') return success(result.session, null, events)
      if (result.status === 'already_finalized') return success(null, result.record)
      if (result.status === 'conflict') {
        activeCheckpoint = null
        return failure('session_conflict', '训练状态已在其他页面更新，请继续最新进度', result.session)
      }
      activeCheckpoint = null
      return failure('no_active_session', '没有可继续的训练')
    } catch {
      activeCheckpoint = null
      return failure('storage_unavailable', '本机训练数据暂时无法读取')
    }
  }

  const buildRecord = async (
    session: TrainingSession,
    outcome: TrainingRecord['outcome'],
    endedAt: Date,
  ): Promise<TrainingRecord> => {
    const totalActiveMilliseconds = activeMilliseconds(session)
    const activeSeconds = Math.round(totalActiveMilliseconds / 1_000)
    const creditedRestSeconds = Math.round(session.creditedRestMilliseconds / 1_000)
    const profile = await persistence.loadProfile()
    const calorie = estimateCalories({
      profile,
      activeMilliseconds: totalActiveMilliseconds,
      creditedRestMilliseconds: session.creditedRestMilliseconds,
    })
    const actions = session.plan.items.map((item, index) =>
      actionResult(item, session.progress[index]),
    )

    return {
      id: session.sessionId,
      outcome,
      plan: clone(session.plan),
      actions,
      activeSeconds,
      creditedRestSeconds,
      trainingDurationSeconds: activeSeconds + creditedRestSeconds,
      completedActionCount: actions.filter((action) => action.status === 'completed').length,
      calorie,
      petId: 'hachimi',
      coachStyleId: session.coachStyleId,
      startedAt: session.startedAt,
      endedAt: endedAt.toISOString(),
    }
  }

  const commitRecord = async (
    previous: TrainingSession,
    terminal: TrainingSession,
    outcome: TrainingRecord['outcome'],
    events: TrainingEvent[],
    now: Date,
  ): Promise<TrainingEngineResult> => {
    try {
      const record = await buildRecord(terminal, outcome, now)
      const result = await persistence.commit({
        sessionId: previous.sessionId,
        expectedRevision: previous.revision,
        record,
      })
      activeCheckpoint = null
      if (result.status === 'committed') return success(null, result.record, events)
      if (result.status === 'already_finalized') return success(null, result.record)
      if (result.status === 'conflict') {
        return failure('session_conflict', '训练状态已在其他页面更新，请继续最新进度', result.session)
      }
      return failure('no_active_session', '没有可继续的训练')
    } catch {
      activeCheckpoint = null
      return failure('storage_unavailable', '本机训练数据暂时无法读取')
    }
  }

  const loadVersioned = async (command: VersionedCommand): Promise<
    | { session: TrainingSession }
    | { result: TrainingEngineResult }
  > => {
    try {
      const session = await persistence.loadCurrent()
      if (!session) {
        const record = await persistence.loadRecord(command.sessionId)
        return {
          result: record
            ? success(null, record)
            : failure('no_active_session', '没有可继续的训练'),
        }
      }
      if (
        session.sessionId !== command.sessionId
        || session.revision !== command.expectedRevision
      ) {
        activeCheckpoint = null
        return {
          result: failure('session_conflict', '训练状态已更新，请继续最新进度', session),
        }
      }
      return { session }
    } catch {
      return { result: failure('storage_unavailable', '本机训练数据暂时无法读取') }
    }
  }

  const completeSet = async (
    previous: TrainingSession,
    flushed: TrainingSession,
    now: Date,
  ): Promise<TrainingEngineResult> => {
    const item = flushed.plan.items[flushed.currentItemIndex]
    const progress = flushed.progress[flushed.currentItemIndex]
    const completedSetIndex = flushed.currentSetIndex
    progress.completedSets += 1
    progress.activeMilliseconds += flushed.currentSetActiveMilliseconds
    flushed.currentSetActiveMilliseconds = 0

    const targetSets = item.sets.value ?? 0
    const completesAction = progress.completedSets >= targetSets
    const completesSession = completesAction
      && flushed.currentItemIndex >= flushed.plan.items.length - 1
    const events: TrainingEvent[] = [
      { type: 'set.completed', itemId: item.id, setIndex: completedSetIndex },
    ]

    if (completesSession) {
      const terminal = withRevision({
        ...flushed,
        activeStartedAt: null,
      }, now)
      events.push({ type: 'session.completed', recordId: flushed.sessionId })
      return commitRecord(previous, terminal, 'completed', events, now)
    }

    const nextItemIndex = completesAction
      ? flushed.currentItemIndex + 1
      : flushed.currentItemIndex
    const nextSetIndex = completesAction ? 0 : flushed.currentSetIndex + 1
    const restSeconds = item.restSeconds.value ?? 0
    const restStartsAt = now.toISOString()
    const restEndsAt = new Date(now.getTime() + restSeconds * 1_000).toISOString()
    let next = withRevision({
      ...flushed,
      status: restSeconds === 0 ? 'ready_to_continue' : 'resting',
      pauseReason: null,
      currentItemIndex: nextItemIndex,
      currentSetIndex: nextSetIndex,
      activeStartedAt: null,
      restStartedAt: restSeconds === 0 ? null : restStartsAt,
      restEndsAt: restSeconds === 0 ? null : restEndsAt,
      scheduledRestSeconds: restSeconds === 0 ? null : restSeconds,
    }, now)
    if (restSeconds === 0) next = afterRest(next, now)
    if (restSeconds > 0) events.push({ type: 'rest.started', endsAt: restEndsAt })
    activeCheckpoint = null
    return commitSession(previous, next, events)
  }

  const dispatch = async (command: TrainingCommand): Promise<TrainingEngineResult> => {
    if (command.type === 'session.create') {
      const issues = validatePlan(command.plan)
      if (issues.length) {
        return failure('invalid_plan', '方案还不能开始训练', null, issues)
      }
      const now = clock.now().toISOString()
      const session: TrainingSession = {
        id: 'current',
        sessionId: idFactory(),
        revision: 0,
        flowVersion: command.flowVersion,
        status: 'paused',
        pauseReason: 'before_start',
        plan: clone(command.plan),
        currentItemIndex: 0,
        currentSetIndex: 0,
        currentSetActiveMilliseconds: 0,
        activeStartedAt: null,
        restStartedAt: null,
        restEndsAt: null,
        scheduledRestSeconds: null,
        creditedRestMilliseconds: 0,
        progress: command.plan.items.map((item) => ({
          itemId: item.id,
          completedSets: 0,
          activeMilliseconds: 0,
          skipped: false,
        })),
        petId: 'hachimi',
        coachStyleId: command.coachStyleId,
        startedAt: now,
        updatedAt: now,
      }
      try {
        const created = await persistence.createCurrent(session)
        if (created.status === 'exists') {
          return failure('active_session_exists', '已有一场未完成训练', created.session)
        }
        return success(created.session)
      } catch {
        return failure('storage_unavailable', '本机训练数据暂时无法读取')
      }
    }

    const loaded = await loadVersioned(command)
    if ('result' in loaded) return loaded.result
    const previous = loaded.session
    const now = clock.now()

    if (command.type === 'playback.select') {
      const item = previous.plan.items[previous.currentItemIndex]
      if (previous.status !== 'paused' || item.id !== command.itemId
        || playbackFingerprint(item) !== command.fingerprint || !validSelection(item, command.range)) {
        return failure('invalid_transition', '动作或片段已变化，请重新选择', previous)
      }
      const next = clone(previous)
      next.plan.items[next.currentItemIndex].playbackSelection = command.range
      return commitSession(previous, withRevision(next, now), [], true)
    }

    if (command.type === 'set.prepare') {
      if (previous.status !== 'paused' && previous.status !== 'ready_to_continue') {
        return failure('invalid_transition', '请先暂停再准备开始', previous)
      }
      return commitSession(previous, withRevision(prepare(previous, now), now), [])
    }

    if (command.type === 'set.start') {
      if (previous.status !== 'paused' && previous.status !== 'ready_to_continue') {
        return failure('invalid_transition', '当前还不能开始下一组', previous)
      }
      if ((previous.pausedRestSeconds ?? 0) > 0) {
        const seconds = previous.pausedRestSeconds!
        const endsAt = new Date(now.getTime() + seconds * 1_000).toISOString()
        return commitSession(previous, withRevision({ ...previous, status: 'resting',
          pauseReason: null, pausedRestSeconds: 0, pendingCountdown: false,
          restStartedAt: now.toISOString(), restEndsAt: endsAt, scheduledRestSeconds: seconds }, now), [])
      }
      const firstStart = previous.pauseReason === 'before_start'
      const next = withRevision({
        ...previous,
        status: 'active',
        pauseReason: null,
        activeStartedAt: now.toISOString(),
        restStartedAt: null,
        restEndsAt: null,
        scheduledRestSeconds: null,
        countdownEndsAt: null,
        pendingCountdown: false,
      }, now)
      const item = next.plan.items[next.currentItemIndex]
      const events: TrainingEvent[] = [
        ...(firstStart
          ? [{ type: 'session.started', sessionId: next.sessionId } as const]
          : []),
        { type: 'set.started', itemId: item.id, setIndex: next.currentSetIndex },
      ]
      const result = await commitSession(previous, next, events)
      if (result.ok) {
        activeCheckpoint = {
          sessionId: next.sessionId,
          milliseconds: clock.monotonicMilliseconds(),
        }
      }
      return result
    }

    if (command.type === 'set.complete') {
      if (previous.status !== 'active') {
        return failure('invalid_transition', '当前没有正在执行的一组', previous)
      }
      const item = previous.plan.items[previous.currentItemIndex]
      if (item.mode !== 'reps') {
        return failure('invalid_transition', '时长型动作会在倒计时结束时完成', previous)
      }
      return completeSet(previous, flushActive(clone(previous)), now)
    }

    if (command.type === 'clock.tick') {
      if (previous.status === 'countdown') {
        const deadline = previous.countdownEndsAt ? Date.parse(previous.countdownEndsAt) : NaN
        if (now.getTime() < deadline) return success(previous)
        // A throttled/suspended tab must not start or catch up a workout.
        if (!Number.isFinite(deadline) || now.getTime() - deadline > 2_000) {
          return commitSession(previous, withRevision({ ...previous, status: 'paused',
            pauseReason: 'recovered', countdownEndsAt: null }, now), [])
        }
        const next = withRevision({ ...previous, status: 'active', pauseReason: null,
          pendingCountdown: false, countdownEndsAt: null, activeStartedAt: now.toISOString() }, now)
        const result = await commitSession(previous, next, [{ type: 'set.started',
          itemId: next.plan.items[next.currentItemIndex].id, setIndex: next.currentSetIndex }])
        if (result.ok) activeCheckpoint = { sessionId: next.sessionId, milliseconds: clock.monotonicMilliseconds() }
        return result
      }
      if (previous.status === 'resting') {
        if (!previous.restEndsAt || now.getTime() < Date.parse(previous.restEndsAt)) {
          return success(previous)
        }
        const settled = settleRest(clone(previous), now, 'ready_to_continue')
        const next = withRevision(afterRest(settled, now), now)
        return commitSession(previous, next, [{ type: 'rest.finished' }])
      }
      if (previous.status !== 'active') {
        return failure('invalid_transition', '当前不需要记录动作时间', previous)
      }
      if (previous.flowVersion === 'watch-v1' && (!activeCheckpoint
        || clock.monotonicMilliseconds() - activeCheckpoint.milliseconds > 5_000)) {
        activeCheckpoint = null
        return commitSession(previous, withRevision({ ...previous, status: 'paused',
          pauseReason: 'recovered', activeStartedAt: null }, now), [
          { type: 'session.paused', reason: 'recovered' },
        ])
      }
      const flushed = flushActive(clone(previous))
      const item = flushed.plan.items[flushed.currentItemIndex]
      const durationMilliseconds = (item.durationSeconds.value ?? 0) * 1_000
      if (
        item.mode === 'duration'
        && flushed.currentSetActiveMilliseconds >= durationMilliseconds
      ) {
        flushed.currentSetActiveMilliseconds = durationMilliseconds
        return completeSet(previous, flushed, now)
      }
      return commitSession(previous, withRevision(flushed, now), [])
    }

    if (command.type === 'session.pause') {
      if (!['active', 'resting', 'countdown', 'ready_to_continue'].includes(previous.status)) {
        return failure('invalid_transition', '当前训练没有在进行', previous)
      }
      if (previous.status === 'resting' && command.reason === 'page_hidden') {
        return commitSession(previous, withRevision({ ...previous, autoAdvanceSuspended: true }, now), [])
      }
      const flushed = previous.status === 'resting'
        ? settleRest(clone(previous), now, 'ready_to_continue')
        : flushActive(clone(previous))
      const next = withRevision({
        ...flushed,
        status: 'paused',
        pauseReason: command.reason,
        activeStartedAt: null,
        countdownEndsAt: null,
        pendingCountdown: previous.status !== 'active' || previous.pendingCountdown,
        pausedRestSeconds: previous.status === 'resting' && previous.restEndsAt
          ? Math.max(0, Math.ceil((Date.parse(previous.restEndsAt) - now.getTime()) / 1_000)) : 0,
      }, now)
      activeCheckpoint = null
      return commitSession(previous, next, [
        { type: 'session.paused', reason: command.reason },
      ])
    }

    if (command.type === 'rest.continue') {
      if (previous.status !== 'resting' && previous.status !== 'ready_to_continue') {
        return failure('invalid_transition', '当前不在组间休息', previous)
      }
      const continuingFromRest = previous.status === 'resting'
      if (previous.flowVersion === 'watch-v1') {
        const settled = continuingFromRest ? settleRest(clone(previous), now, 'ready_to_continue') : clone(previous)
        return commitSession(previous, withRevision(prepare(settled, now), now),
          continuingFromRest ? [{ type: 'rest.finished' }] : [])
      }
      const active = continuingFromRest
        ? settleRest(clone(previous), now, 'active')
        : {
            ...clone(previous),
            status: 'active' as const,
            pauseReason: null,
            activeStartedAt: now.toISOString(),
          }
      const next = withRevision(active, now)
      const item = next.plan.items[next.currentItemIndex]
      const events: TrainingEvent[] = [
        ...(continuingFromRest ? [{ type: 'rest.finished' } as const] : []),
        { type: 'set.started', itemId: item.id, setIndex: next.currentSetIndex },
      ]
      const result = await commitSession(previous, next, events)
      if (result.ok) {
        activeCheckpoint = {
          sessionId: next.sessionId,
          milliseconds: clock.monotonicMilliseconds(),
        }
      }
      return result
    }

    if (command.type === 'action.skip') {
      if (previous.status !== 'active') {
        return failure('invalid_transition', '只有正在训练时可以跳过剩余组', previous)
      }
      if (previous.currentItemIndex >= previous.plan.items.length - 1) {
        return failure(
          'end_confirmation_required',
          '这是最后一个动作，跳过将提前结束训练',
          previous,
        )
      }
      const skipped = flushActive(clone(previous))
      const item = skipped.plan.items[skipped.currentItemIndex]
      const progress = skipped.progress[skipped.currentItemIndex]
      progress.activeMilliseconds += skipped.currentSetActiveMilliseconds
      progress.skipped = true
      const next = withRevision({
        ...skipped,
        status: 'paused',
        pauseReason: 'between_actions',
        currentItemIndex: skipped.currentItemIndex + 1,
        currentSetIndex: 0,
        currentSetActiveMilliseconds: 0,
        activeStartedAt: null,
        restStartedAt: null,
        restEndsAt: null,
        scheduledRestSeconds: null,
      }, now)
      activeCheckpoint = null
      return commitSession(previous, next, [{ type: 'action.skipped', itemId: item.id }])
    }

    if (command.type === 'session.end_early') {
      let terminal = clone(previous)
      if (terminal.status === 'active') {
        terminal = flushActive(terminal)
      } else if (terminal.status === 'resting') {
        terminal = settleRest(terminal, now, 'ready_to_continue')
      }
      if (terminal.currentSetActiveMilliseconds > 0) {
        const progress = terminal.progress[terminal.currentItemIndex]
        progress.activeMilliseconds += terminal.currentSetActiveMilliseconds
        terminal.currentSetActiveMilliseconds = 0
      }
      terminal = withRevision({ ...terminal, activeStartedAt: null }, now)
      activeCheckpoint = null
      return commitRecord(
        previous,
        terminal,
        'ended_early',
        [{ type: 'session.ended_early', recordId: terminal.sessionId }],
        now,
      )
    }

    return failure('invalid_transition', '当前操作暂不可用', previous)
  }

  const restore = async (): Promise<TrainingEngineResult> => {
    try {
      const current = await persistence.loadCurrent()
      if (!current) return success(null)
      activeCheckpoint = null
      const now = clock.now()
      if (current.status === 'active' || current.status === 'countdown') {
        const next = withRevision({
          ...current,
          status: 'paused',
          pauseReason: 'recovered',
          activeStartedAt: null,
          countdownEndsAt: null,
        }, now)
        return commitSession(current, next, [{ type: 'session.paused', reason: 'recovered' }])
      }
      if (
        current.status === 'resting'
        && current.restEndsAt
        && now.getTime() >= Date.parse(current.restEndsAt)
      ) {
        const next = withRevision(settleRest(clone(current), now, 'ready_to_continue'), now)
        return commitSession(current, next, [{ type: 'rest.finished' }])
      }
      if (current.status === 'resting' && current.flowVersion === 'watch-v1') {
        const next = withRevision({ ...current, autoAdvanceSuspended: true }, now)
        return commitSession(current, next, [])
      }
      return success(current)
    } catch {
      return failure('storage_unavailable', '本机训练数据暂时无法读取')
    }
  }

  return { restore, dispatch }
}
