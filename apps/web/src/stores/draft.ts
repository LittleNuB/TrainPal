import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  AdjustableField,
  ApplyAdjustmentResult,
  AppliedAdjustment,
  DraftPersonalizationState,
  RestoreAdjustmentResult,
} from '@/domain/personalization'
import type {
  ActionMode,
  AnalysisCandidate,
  DraftItem,
  DraftPlan,
  DraftRepository,
  LocalMediaFingerprint,
  Segment,
  SourceSummary,
  SourcedValue,
} from '@/domain/types'
import { toSafeOriginUrl } from '@/domain/source'
import { normalizeDraftItems } from '@/domain/plan'
import { QUICK_EXPERIENCE_PLAN_NAME } from '@/features/quick-experience/fixture'
import {
  fingerprintDraftPlan,
  proposePlanAdjustment,
  type AdjustmentPolicyInput,
  type AdjustmentProposal,
} from '@/personalization/adjustment-policy'

type NumericField = 'sets' | 'reps' | 'durationSeconds' | 'restSeconds' | 'weightKg'
type PersistState = 'idle' | 'pending' | 'saving' | 'saved' | 'failed'
type ProposalStrategy = 'append' | 'replace'

const emptyPersonalization = (): DraftPersonalizationState => ({
  proposal: null,
  applied: null,
})

const emptyPlan = (): DraftPlan => ({
  id: 'current',
  name: '未命名方案',
  linkedPlanId: null,
  items: [],
  revision: 0,
  personalization: emptyPersonalization(),
  updatedAt: new Date(0).toISOString(),
})

const id = (): string => globalThis.crypto?.randomUUID?.() ?? `item-${Date.now()}-${Math.random()}`

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

const normalizePlan = (nextPlan: DraftPlan): DraftPlan => ({
  ...cloneJson(nextPlan),
  revision: Number.isInteger(nextPlan.revision) && (nextPlan.revision ?? 0) >= 0
    ? nextPlan.revision
    : 0,
  personalization: nextPlan.personalization
    ? cloneJson(nextPlan.personalization)
    : emptyPersonalization(),
  items: normalizeDraftItems(nextPlan.items, nextPlan.name === QUICK_EXPERIENCE_PLAN_NAME),
})

const sourced = <T>(value: T | null, source: SourcedValue<T>['source']): SourcedValue<T> => ({
  value,
  source,
})

type SourceSnapshot = Pick<SourceSummary, 'title' | 'origin_url'> & {
  kind?: 'controlled' | 'local'
  localMedia?: LocalMediaFingerprint
}

const fromCandidate = (
  candidate: AnalysisCandidate,
  source?: SourceSnapshot,
): DraftItem => {
  const modeMissing = candidate.parameters.mode === null
  const mode: ActionMode = candidate.parameters.mode ?? 'reps'
  const isReps = mode === 'reps'
  return {
    id: id(),
    name: candidate.name,
    sourceParameters: cloneJson(candidate.parameters),
    sourceEvidence: cloneJson(candidate.evidence),
    sourceTips: cloneJson(candidate.tips ?? []),
    playbackOptions: cloneJson(candidate.playback_options ?? []),
    parameterConflicts: cloneJson(candidate.parameter_conflicts ?? []),
    sourceRef: {
      sourceId: candidate.source_id,
      title: source?.title,
      originUrl: toSafeOriginUrl(source?.origin_url),
      ...(source?.kind ? { kind: source.kind } : {}),
      ...(source?.localMedia ? { localMedia: cloneJson(source.localMedia) } : {}),
    },
    segment: sourced(
      candidate.segment ? cloneJson(candidate.segment) : null,
      candidate.segment ? 'video' : null,
    ),
    confirmationStatus: candidate.needs_confirmation || modeMissing ? 'pending' : 'confirmed',
    mode,
    sets: candidate.parameters.sets === null
      ? sourced(3, 'rule')
      : sourced(candidate.parameters.sets, 'video'),
    reps: isReps
      ? candidate.parameters.reps === null
        ? sourced(10, 'rule')
        : sourced(candidate.parameters.reps,
          candidate.parameters.reps_max != null && candidate.parameters.reps_max > candidate.parameters.reps
            ? 'rule' : 'video')
      : sourced<number>(null, null),
    durationSeconds: !isReps
      ? candidate.parameters.duration_seconds === null
        ? sourced(30, 'rule')
        : sourced(candidate.parameters.duration_seconds, 'video')
      : sourced<number>(null, null),
    restSeconds: candidate.parameters.rest_seconds === null
      ? sourced(60, 'rule')
      : sourced(candidate.parameters.rest_seconds, 'video'),
    weightKg: sourced<number>(null, null),
  }
}

export const useDraftStore = defineStore('draft', () => {
  const plan = ref<DraftPlan>(emptyPlan())
  const loaded = ref(false)
  const persistState = ref<PersistState>('idle')
  const adjustmentPending = ref(false)
  let repository: DraftRepository | undefined
  let persistTimer: ReturnType<typeof setTimeout> | undefined
  let persistInFlight: Promise<DraftPlan | void> | null = null
  let persistenceSuspended = false
  let adjustmentSettled: Promise<void> | null = null

  const items = computed(() => plan.value.items)
  const adjustmentProposal = computed(() => plan.value.personalization?.proposal ?? null)
  const appliedAdjustment = computed(() => plan.value.personalization?.applied ?? null)
  const persistMessage = computed(() => {
    if (persistState.value === 'failed') return '未保存，点击重试'
    if (persistState.value === 'pending' || persistState.value === 'saving') {
      return '正在保存到本机…'
    }
    if (persistState.value === 'saved') return '已自动保存到本机'
    return '还没有需要保存的修改'
  })

  async function load(nextRepository: DraftRepository): Promise<void> {
    repository = nextRepository
    const stored = await repository.load()
    plan.value = stored ? normalizePlan(stored) : emptyPlan()
    persistState.value = plan.value.items.length ? 'saved' : 'idle'
    loaded.value = true
  }

  async function reload(): Promise<void> {
    if (!repository) return
    const stored = await repository.load()
    plan.value = stored ? normalizePlan(stored) : emptyPlan()
    persistState.value = plan.value.items.length ? 'saved' : 'idle'
  }

  function schedulePersist(): void {
    if (!repository || persistenceSuspended) {
      return
    }
    if (persistTimer) {
      clearTimeout(persistTimer)
    }
    persistState.value = 'pending'
    persistTimer = setTimeout(() => {
      void flushPersist().catch(() => undefined)
    }, 300)
  }

  function markContentChanged(): void {
    plan.value.revision = (plan.value.revision ?? 0) + 1
    const personalization = plan.value.personalization ?? emptyPersonalization()
    personalization.proposal = null
    plan.value.personalization = personalization
    schedulePersist()
  }

  async function flushPersist(): Promise<void> {
    while (adjustmentSettled) await adjustmentSettled
    await persistCurrentPlan()
  }

  async function persistCurrentPlan(expectedPlan?: DraftPlan): Promise<boolean> {
    if (!repository || persistenceSuspended) {
      return false
    }
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    while (persistInFlight) {
      await persistInFlight.catch(() => undefined)
      if (persistenceSuspended) return false
    }
    if (expectedPlan && plan.value !== expectedPlan) return false
    persistState.value = 'saving'
    plan.value.updatedAt = new Date().toISOString()
    const savingPlan = plan.value
    const savingRevision = savingPlan.revision
    const snapshot = cloneJson(savingPlan)
    const operation = repository.save(snapshot)
    persistInFlight = operation
    try {
      const saved = await operation
      if (saved && plan.value === savingPlan) {
        plan.value.linkedPlanId = saved.linkedPlanId
        if (plan.value.name === snapshot.name) plan.value.name = saved.name
      }
      if (plan.value === savingPlan && plan.value.revision === savingRevision) {
        persistState.value = 'saved'
      }
      return true
    } catch (error) {
      if (plan.value === savingPlan) persistState.value = 'failed'
      throw error
    } finally {
      if (persistInFlight === operation) persistInFlight = null
    }
  }

  async function retryPersist(): Promise<void> {
    try {
      await flushPersist()
    } catch {
      // The visible failed state remains available for another user retry.
    }
  }

  async function quiescePersistence(): Promise<void> {
    persistenceSuspended = true
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    await persistInFlight?.catch(() => undefined)
    await adjustmentSettled
  }

  function resumePersistence(): void {
    persistenceSuspended = false
    if (persistState.value === 'pending') schedulePersist()
  }

  function adoptPersistedPlan(nextPlan: DraftPlan): void {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    plan.value = normalizePlan(nextPlan)
    persistState.value = plan.value.items.length ? 'saved' : 'idle'
  }

  function resetLocalState(keepSuspended = false): void {
    if (persistTimer) {
      clearTimeout(persistTimer)
      persistTimer = undefined
    }
    plan.value = emptyPlan()
    persistState.value = 'idle'
    persistenceSuspended = keepSuspended
  }

  function updatePlanName(name: string): void {
    const normalized = name.trim()
    if (!normalized) return
    plan.value.name = normalized
    markContentChanged()
  }

  function applyCandidateProposal(
    candidates: AnalysisCandidate[],
    sources: Record<string, SourceSnapshot> = {},
    strategy: ProposalStrategy = 'append',
  ): void {
    const ordered = [...candidates].sort((left, right) => (
      left.segment.start_seconds - right.segment.start_seconds
      || left.segment.end_seconds - right.segment.end_seconds
    ))
    const proposalItems = ordered.map((candidate) =>
      fromCandidate(candidate, sources[candidate.source_id]),
    )
    if (strategy === 'replace') {
      plan.value = {
        ...emptyPlan(),
        revision: plan.value.revision,
        items: proposalItems,
      }
    } else {
      plan.value.items.push(...proposalItems)
    }
    markContentChanged()
  }

  function confirmItem(itemId: string): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item || item.confirmationStatus !== 'pending') return
    item.confirmationStatus = 'confirmed'
    markContentChanged()
  }

  function addManualAction(input: { name: string; mode: ActionMode }): void {
    const isReps = input.mode === 'reps'
    plan.value.items.push({
      id: id(),
      name: input.name.trim(),
      sourceRef: null,
      segment: sourced<Segment>(null, null),
      mode: input.mode,
      sets: sourced(3, 'rule'),
      reps: isReps ? sourced(10, 'rule') : sourced<number>(null, null),
      durationSeconds: isReps ? sourced<number>(null, null) : sourced(30, 'rule'),
      restSeconds: sourced(60, 'rule'),
      weightKg: sourced<number>(null, null),
    })
    markContentChanged()
  }

  function updateValue(itemId: string, field: NumericField, value: number | null): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item) {
      return
    }
    item[field] = sourced(value, value === null ? null : 'user')
    markContentChanged()
  }

  function updateName(itemId: string, name: string): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item) {
      return
    }
    item.name = name.trim()
    markContentChanged()
  }

  function updateMode(itemId: string, mode: ActionMode): void {
    const item = plan.value.items.find((entry) => entry.id === itemId)
    if (!item || item.mode === mode) {
      return
    }
    item.mode = mode
    item.reps = mode === 'reps' ? sourced(10, 'rule') : sourced<number>(null, null)
    item.durationSeconds = mode === 'duration' ? sourced(30, 'rule') : sourced<number>(null, null)
    markContentChanged()
  }

  function move(itemId: string, direction: -1 | 1): void {
    const index = plan.value.items.findIndex((entry) => entry.id === itemId)
    const target = index + direction
    if (index < 0 || target < 0 || target >= plan.value.items.length) {
      return
    }
    const [item] = plan.value.items.splice(index, 1)
    plan.value.items.splice(target, 0, item)
    markContentChanged()
  }

  function duplicate(itemId: string): void {
    const index = plan.value.items.findIndex((entry) => entry.id === itemId)
    if (index < 0) {
      return
    }
    const copy = cloneJson(plan.value.items[index])
    copy.id = id()
    copy.name = `${copy.name}（副本）`
    plan.value.items.splice(index + 1, 0, copy)
    markContentChanged()
  }

  function remove(itemId: string): void {
    plan.value.items = plan.value.items.filter((entry) => entry.id !== itemId)
    markContentChanged()
  }

  function proposeAdjustment(
    input: Omit<AdjustmentPolicyInput, 'basePlan'>,
  ): AdjustmentProposal {
    const generated = proposePlanAdjustment({ ...input, basePlan: cloneJson(plan.value) })
    const existing = plan.value.personalization?.proposal
    if (existing?.id === generated.id && existing.basePlanFingerprint === generated.basePlanFingerprint) {
      return existing
    }
    const personalization = plan.value.personalization ?? emptyPersonalization()
    personalization.proposal = cloneJson(generated)
    plan.value.personalization = personalization
    schedulePersist()
    return generated
  }

  function discardAdjustmentProposal(): void {
    if (!plan.value.personalization?.proposal) return
    plan.value.personalization.proposal = null
    schedulePersist()
  }

  const changeKey = (itemId: string, field: AdjustableField): string => `${itemId}:${field}`

  type AdjustmentWrite = { item: DraftItem; field: AdjustableField; before: SourcedValue<number>; after: SourcedValue<number> }

  async function persistAdjustment(previousPlan: DraftPlan, writes: AdjustmentWrite[]): Promise<boolean> {
    const targetPlan = plan.value
    const targetRevision = targetPlan.revision
    const targetFingerprint = fingerprintDraftPlan(targetPlan)
    const targetPersonalization = targetPlan.personalization
    const targetProposal = targetPersonalization?.proposal
    let settle!: () => void
    adjustmentSettled = new Promise<void>((resolve) => { settle = resolve })
    adjustmentPending.value = true
    try {
      if (!await persistCurrentPlan(targetPlan)) throw new Error('adjustment save interrupted')
      return plan.value === targetPlan
    } catch {
      // Never replace a newly loaded/cleared plan or undo edits made during the write.
      if (plan.value === targetPlan) {
        if (targetPlan.revision === targetRevision
          && fingerprintDraftPlan(targetPlan) === targetFingerprint
          && targetPlan.personalization === targetPersonalization
          && targetPlan.personalization?.proposal === targetProposal) {
          plan.value = previousPlan
        } else {
          for (const write of writes) {
            if (targetPlan.items.includes(write.item) && write.item[write.field] === write.after) {
              write.item[write.field] = cloneJson(write.before)
            }
          }
          targetPlan.revision = (targetPlan.revision ?? 0) + 1
          targetPlan.personalization = {
            proposal: null,
            applied: cloneJson(previousPlan.personalization?.applied ?? null),
          }
        }
      }
      return false
    } finally {
      // Autosaves must see the settled result, never the provisional failed adjustment.
      adjustmentPending.value = false
      adjustmentSettled = null
      settle()
    }
  }

  async function applyAdjustmentProposal(proposalId: string): Promise<ApplyAdjustmentResult> {
    if (adjustmentPending.value || persistenceSuspended || !repository) return 'conflict'
    const proposal = plan.value.personalization?.proposal
    if (
      !proposal
      || proposal.id !== proposalId
      || proposal.status !== 'ready'
      || proposal.basePlanRevision !== (plan.value.revision ?? 0)
      || proposal.basePlanFingerprint !== fingerprintDraftPlan(plan.value)
    ) return 'conflict'

    for (const change of proposal.changes) {
      const item = plan.value.items.find((candidate) => candidate.id === change.itemId)
      const current = item?.[change.field]
      if (
        !item
        || current?.value !== change.before.value
        || current.source !== change.before.source
      ) return 'conflict'
    }

    const previousPlan = cloneJson(plan.value)
    const writes: AdjustmentWrite[] = []
    const previous = plan.value.personalization?.applied
    const baseByKey = new Map(
      (previous?.baseValues ?? []).map((entry) => [changeKey(entry.itemId, entry.field), entry]),
    )
    const appliedByKey = new Map(
      (previous?.appliedValues ?? []).map((entry) => [changeKey(entry.itemId, entry.field), entry]),
    )
    for (const change of proposal.changes) {
      const item = plan.value.items.find((candidate) => candidate.id === change.itemId)!
      const key = changeKey(change.itemId, change.field)
      if (!baseByKey.has(key)) {
        baseByKey.set(key, {
          itemId: change.itemId,
          field: change.field,
          value: change.before.value,
          source: change.before.source,
        })
      }
      const before = cloneJson(item[change.field])
      item[change.field] = cloneJson(change.after)
      writes.push({ item, field: change.field, before, after: item[change.field] })
      appliedByKey.set(key, {
        itemId: change.itemId,
        field: change.field,
        value: change.after.value,
      })
    }

    const applied: AppliedAdjustment = {
      proposalId: proposal.id,
      policyVersion: proposal.policyVersion,
      intent: proposal.intent,
      baseValues: [...baseByKey.values()],
      appliedValues: [...appliedByKey.values()],
      appliedAt: new Date().toISOString(),
    }
    plan.value.revision = (plan.value.revision ?? 0) + 1
    plan.value.personalization = { proposal: null, applied }
    return await persistAdjustment(previousPlan, writes) ? 'applied' : 'persist_failed'
  }

  async function restoreBasePlan(): Promise<RestoreAdjustmentResult> {
    if (adjustmentPending.value || persistenceSuspended || !repository) return { status: 'busy' }
    const applied = plan.value.personalization?.applied
    if (!applied) return { status: 'nothing_to_restore' }
    const previousPlan = cloneJson(plan.value)
    const writes: AdjustmentWrite[] = []
    const appliedByKey = new Map(
      applied.appliedValues.map((entry) => [changeKey(entry.itemId, entry.field), entry]),
    )
    let restored = 0
    for (const base of applied.baseValues) {
      const item = plan.value.items.find((candidate) => candidate.id === base.itemId)
      const current = item?.[base.field]
      const expected = appliedByKey.get(changeKey(base.itemId, base.field))
      if (
        !item
        || !current
        || !expected
        || current.source !== 'personalized'
        || current.value !== expected.value
      ) continue
      item[base.field] = { value: base.value, source: base.source }
      writes.push({ item, field: base.field, before: cloneJson(current), after: item[base.field] })
      restored += 1
    }
    plan.value.revision = (plan.value.revision ?? 0) + 1
    plan.value.personalization = { proposal: null, applied: null }
    return await persistAdjustment(previousPlan, writes)
      ? { status: 'restored', count: restored }
      : { status: 'persist_failed' }
  }

  return {
    plan,
    items,
    loaded,
    persistState,
    persistMessage,
    adjustmentProposal,
    appliedAdjustment,
    adjustmentPending,
    load,
    reload,
    flushPersist,
    retryPersist,
    quiescePersistence,
    resumePersistence,
    adoptPersistedPlan,
    resetLocalState,
    updatePlanName,
    applyCandidateProposal,
    confirmItem,
    addManualAction,
    updateValue,
    updateName,
    updateMode,
    move,
    duplicate,
    remove,
    proposeAdjustment,
    discardAdjustmentProposal,
    applyAdjustmentProposal,
    restoreBasePlan,
  }
})
