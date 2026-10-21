import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type { TrainingSession } from '@/domain/training'
import type { DraftPlan } from '@/domain/types'
import { QUICK_EXPERIENCE_PLAN_NAME } from '@/features/quick-experience/fixture'
import type {
  TrainingCommand,
  TrainingEngine,
  TrainingEngineResult,
} from '@/training/training-engine'
import { useTrainingStore } from '@/stores/training'

const activeSession = (revision: number): TrainingSession => ({
  id: 'current',
  sessionId: 'session-1',
  revision,
  status: 'active',
  pauseReason: null,
  plan: { name: '并发测试', source: 'draft', sourcePlanId: null, items: [] },
  currentItemIndex: 0,
  currentSetIndex: 0,
  currentSetActiveMilliseconds: 0,
  activeStartedAt: '2026-07-21T00:00:00.000Z',
  restStartedAt: null,
  restEndsAt: null,
  scheduledRestSeconds: null,
  creditedRestMilliseconds: 0,
  progress: [],
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  updatedAt: '2026-07-21T00:00:00.000Z',
})

const success = (session: TrainingSession): TrainingEngineResult => ({
  ok: true,
  session,
  record: null,
  events: [],
})

class DeferredTickEngine implements TrainingEngine {
  commands: TrainingCommand[] = []
  resolveTick!: () => void

  async restore(): Promise<TrainingEngineResult> {
    return success(activeSession(1))
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    this.commands.push(command)
    if (command.type === 'clock.tick') {
      await new Promise<void>((resolve) => {
        this.resolveTick = resolve
      })
      return success(activeSession(2))
    }
    if (command.type === 'session.pause') {
      return success({
        ...activeSession(3),
        status: 'paused',
        pauseReason: command.reason,
        activeStartedAt: null,
      })
    }
    return success(activeSession(1))
  }
}

class RecordThenSessionEngine implements TrainingEngine {
  constructor(private readonly activeAlreadyExists = false) {}

  async restore(): Promise<TrainingEngineResult> {
    return success(activeSession(1))
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    if (command.type === 'session.end_early') {
      return {
        ok: true,
        session: null,
        record: {
          id: 'session-1',
          outcome: 'ended_early',
          plan: activeSession(1).plan,
          actions: [],
          activeSeconds: 0,
          creditedRestSeconds: 0,
          trainingDurationSeconds: 0,
          completedActionCount: 0,
          calorie: { value: 0, method: 'generic' },
          petId: 'hachimi',
          coachStyleId: null,
          startedAt: '2026-07-21T00:00:00.000Z',
          endedAt: '2026-07-21T00:01:00.000Z',
        },
        events: [{ type: 'session.ended_early', recordId: 'session-1' }],
      }
    }
    if (command.type === 'session.create') {
      return this.activeAlreadyExists
        ? {
            ok: false,
            code: 'active_session_exists',
            message: '已有一场未完成训练',
            session: activeSession(2),
          }
        : success(activeSession(2))
    }
    throw new Error('unexpected command')
  }
}

class ConflictEngine implements TrainingEngine {
  commands: TrainingCommand[] = []

  async restore(): Promise<TrainingEngineResult> {
    return success(activeSession(1))
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    this.commands.push(command)
    return {
      ok: false,
      code: 'session_conflict',
      message: '训练状态已在其他页面更新，请继续最新进度',
      session: activeSession(2),
    }
  }
}

class StorageFailureEngine implements TrainingEngine {
  commands: TrainingCommand[] = []

  async restore(): Promise<TrainingEngineResult> {
    return success(activeSession(1))
  }

  async dispatch(command: TrainingCommand): Promise<TrainingEngineResult> {
    this.commands.push(command)
    return {
      ok: false,
      code: 'storage_unavailable',
      message: '本机训练数据暂时无法读取',
      session: null,
    }
  }
}

describe('training store command serialization', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('queues page-hidden pause behind a tick and uses the latest revision', async () => {
    const engine = new DeferredTickEngine()
    const store = useTrainingStore()
    await store.load(engine)

    const ticking = store.tick()
    await Promise.resolve()
    const pausing = store.pause('page_hidden')
    await Promise.resolve()

    expect(engine.commands).toHaveLength(1)
    expect(engine.commands[0]).toMatchObject({ type: 'clock.tick', expectedRevision: 1 })

    engine.resolveTick()
    await ticking
    await pausing

    expect(engine.commands[1]).toMatchObject({
      type: 'session.pause',
      expectedRevision: 2,
      reason: 'page_hidden',
    })
    expect(store.session).toMatchObject({ status: 'paused', pauseReason: 'page_hidden' })
  })

  it('keeps the explicitly confirmed set revision when queued behind another command', async () => {
    const engine = new DeferredTickEngine()
    const store = useTrainingStore()
    await store.load(engine)
    const ticking = store.tick()
    await Promise.resolve()
    const completing = store.completeSet({ sessionId: 'session-1', expectedRevision: 1, itemId: 'curl', setIndex: 0 })
    engine.resolveTick()
    await ticking
    await completing
    expect(engine.commands[1]).toEqual({ type: 'set.complete', sessionId: 'session-1', expectedRevision: 1,
      pausedSet: { itemId: 'curl', setIndex: 0 } })
  })

  it('marks quick experience and linked plans in the immutable snapshot', async () => {
    const engine = new DeferredTickEngine()
    const store = useTrainingStore()
    await store.load(engine)
    engine.commands = []
    const baseDraft: DraftPlan = {
      id: 'current',
      name: QUICK_EXPERIENCE_PLAN_NAME,
      linkedPlanId: null,
      items: [],
      updatedAt: '2026-07-21T00:00:00.000Z',
    }

    await store.createFromDraft(baseDraft, 'zen')
    await store.createFromDraft({ ...baseDraft, name: '已存方案', linkedPlanId: 'plan-1' })

    expect(engine.commands[0]).toMatchObject({
      type: 'session.create',
      coachStyleId: 'zen',
      plan: { source: 'sample', sourcePlanId: null },
    })
    expect(engine.commands[1]).toMatchObject({
      type: 'session.create',
      coachStyleId: null,
      plan: { source: 'saved', sourcePlanId: 'plan-1' },
    })
  })

  it('clears the previous terminal result when a new session is created', async () => {
    const store = useTrainingStore()
    await store.load(new RecordThenSessionEngine())
    await store.endEarly()
    expect(store.lastRecord?.outcome).toBe('ended_early')

    await store.createFromDraft({
      id: 'current',
      name: '下一场训练',
      linkedPlanId: null,
      items: [],
      updatedAt: '2026-07-21T00:02:00.000Z',
    })

    expect(store.lastRecord).toBeNull()
  })

  it('clears the previous result when another tab already created the current session', async () => {
    const store = useTrainingStore()
    await store.load(new RecordThenSessionEngine(true))
    await store.endEarly()
    expect(store.lastRecord?.outcome).toBe('ended_early')

    await store.createFromDraft({
      id: 'current',
      name: '下一场训练',
      linkedPlanId: null,
      items: [],
      updatedAt: '2026-07-21T00:02:00.000Z',
    })

    expect(store.session?.sessionId).toBe('session-1')
    expect(store.lastRecord).toBeNull()
  })

  it('locks the losing tab after a session conflict instead of dispatching another tick', async () => {
    const engine = new ConflictEngine()
    const store = useTrainingStore()
    await store.load(engine)

    await store.completeSet()
    expect(store.conflictLocked).toBe(true)
    expect(store.session?.status).toBe('paused')

    await store.tick()
    expect(engine.commands).toHaveLength(1)
    expect(store.errorMessage).toContain('其他页面')
  })

  it('locally pauses and stops ticking when an active-session write fails', async () => {
    const engine = new StorageFailureEngine()
    const store = useTrainingStore()
    await store.load(engine)

    await store.tick()
    expect(store.commandLocked).toBe(true)
    expect(store.session?.status).toBe('paused')
    expect(store.errorMessage).toContain('此页面已暂停')

    await store.tick()
    expect(engine.commands).toHaveLength(1)
  })
})
