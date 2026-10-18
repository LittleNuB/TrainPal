import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { ActionFeedback } from '@/personalization/feedback'

import type { LibraryRepository } from '@/db/library-repository'
import type { CoachStyleId } from '@/domain/coach'
import type { DraftPlan } from '@/domain/types'
import type {
  Preferences,
  SavedPlan,
  TrainingProfile,
  TrainingRecord,
} from '@/domain/training'
import {
  QUICK_EXPERIENCE_PLAN_NAME,
  createQuickExperienceDraftItems,
} from '@/features/quick-experience/fixture'

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

const emptyProfile = (): TrainingProfile => ({
  id: 'current',
  sex: null,
  age: null,
  heightCm: null,
  weightKg: null,
  updatedAt: new Date(0).toISOString(),
})

const defaultPreferences = (): Preferences => ({
  id: 'current',
  petVisible: true,
  coachStyleId: null,
  updatedAt: new Date(0).toISOString(),
})

export const useLibraryStore = defineStore('library', () => {
  const actionFeedback = ref<ActionFeedback[]>([])
  const feedbackRevision = ref(0)
  const plans = ref<SavedPlan[]>([])
  const records = ref<TrainingRecord[]>([])
  const profile = ref<TrainingProfile>(emptyProfile())
  const preferences = ref<Preferences>(defaultPreferences())
  const loaded = ref(false)
  const persistenceSuspended = ref(false)
  const preferencePending = ref(false)
  let repository: LibraryRepository | null = null
  let plansRevision = 0
  let preferenceQueue: Promise<void> = Promise.resolve()
  let preferenceWrites = 0
  let forceSpeechOff = false
  const operations = new Set<Promise<unknown>>()

  const requireRepository = (): LibraryRepository => {
    if (!repository) throw new Error('local training library is not loaded')
    return repository
  }

  const runOperation = <T>(operation: () => Promise<T>): Promise<T> => {
    if (persistenceSuspended.value) {
      return Promise.reject(new Error('local data persistence is suspended'))
    }
    const pending = operation()
    operations.add(pending)
    void pending.then(
      () => operations.delete(pending),
      () => operations.delete(pending),
    )
    return pending
  }

  const applyPreferences = (saved: Preferences): void => {
    preferences.value = forceSpeechOff
      ? { ...saved, speechReminders: { automatic_speech: false, confirmed: true } }
      : saved
  }

  const preferenceFields = (current: Preferences): Omit<Preferences, 'id' | 'updatedAt'> => {
    const { id: _id, updatedAt: _updatedAt, ...editable } = cloneJson(current)
    return editable
  }

  const savePreference = (update: (current: Preferences) => Omit<Preferences, 'id' | 'updatedAt'>): Promise<void> =>
    runOperation(async () => {
      preferenceWrites += 1
      preferencePending.value = true
      const pending = preferenceQueue.then(async () => {
        applyPreferences(await requireRepository().savePreferences(update(preferences.value)))
      })
      preferenceQueue = pending.catch(() => undefined)
      try { await pending }
      finally { preferenceWrites -= 1; preferencePending.value = preferenceWrites > 0 }
    })

  async function load(nextRepository: LibraryRepository): Promise<void> {
    repository = nextRepository
    const [savedPlans, savedRecords, savedProfile, savedPreferences, savedFeedback] = await Promise.all([
      repository.listPlans(),
      repository.listRecords(),
      repository.loadProfile(),
      repository.loadPreferences(),
      repository.listActionFeedback(),
    ])
    actionFeedback.value = savedFeedback
    feedbackRevision.value = Math.max(0, ...savedFeedback.map(entry => Date.parse(entry.recordedAt)))
    plans.value = savedPlans
    records.value = savedRecords
    profile.value = savedProfile ?? emptyProfile()
    applyPreferences(savedPreferences ?? defaultPreferences())
    loaded.value = true
  }

  async function reload(): Promise<void> {
    const currentRepository = requireRepository()
    const expectedPlansRevision = plansRevision
    const [savedPlans, savedRecords, savedProfile, savedPreferences, savedFeedback] = await Promise.all([
      currentRepository.listPlans(),
      currentRepository.listRecords(),
      currentRepository.loadProfile(),
      currentRepository.loadPreferences(),
      currentRepository.listActionFeedback(),
    ])
    actionFeedback.value = savedFeedback
    feedbackRevision.value = Math.max(feedbackRevision.value, ...savedFeedback.map(entry => Date.parse(entry.recordedAt)))
    if (plansRevision === expectedPlansRevision) plans.value = savedPlans
    records.value = savedRecords
    profile.value = savedProfile ?? emptyProfile()
    applyPreferences(savedPreferences ?? defaultPreferences())
  }

  async function refreshHistory(): Promise<void> {
    const expectedPlansRevision = plansRevision
    await runOperation(async () => {
      const currentRepository = requireRepository()
      const [savedPlans, savedRecords] = await Promise.all([
        currentRepository.listPlans(),
        currentRepository.listRecords(),
      ])
      if (plansRevision === expectedPlansRevision) plans.value = savedPlans
      records.value = savedRecords
    })
  }

  async function saveCurrentDraftAs(name: string): Promise<DraftPlan> {
    plansRevision += 1
    return runOperation(async () => {
      const result = await requireRepository().saveCurrentDraftAs(name)
      plans.value = [result.plan, ...plans.value.filter((plan) => plan.id !== result.plan.id)]
      return result.draft
    })
  }

  async function openPlan(planId: string): Promise<DraftPlan> {
    return runOperation(() => requireRepository().openPlan(planId))
  }

  async function deletePlan(planId: string): Promise<DraftPlan | null> {
    plansRevision += 1
    return runOperation(async () => {
      const nextDraft = await requireRepository().deletePlan(planId)
      plans.value = plans.value.filter((plan) => plan.id !== planId)
      return nextDraft
    })
  }

  async function useQuickExperience(): Promise<DraftPlan> {
    return runOperation(() => requireRepository().replaceCurrentDraft({
      name: QUICK_EXPERIENCE_PLAN_NAME,
      items: createQuickExperienceDraftItems(),
    }))
  }

  async function replaceCurrentDraft(input: Pick<DraftPlan, 'name' | 'items'>): Promise<DraftPlan> {
    return runOperation(() => requireRepository().replaceCurrentDraft(cloneJson(input)))
  }

  async function saveProfile(
    input: Omit<TrainingProfile, 'id' | 'updatedAt'>,
  ): Promise<void> {
    await runOperation(async () => {
      profile.value = await requireRepository().saveProfile({ ...input,
        trainingExperience: input.trainingExperience === undefined ? (profile.value.trainingExperience ?? null) : input.trainingExperience })
    })
  }

  async function clearProfile(): Promise<void> {
    await saveProfile({ sex: null, age: null, heightCm: null, weightKg: null, trainingExperience: null })
  }

  async function saveActionFeedback(recordId: string, itemId: string, value: ActionFeedback['value'] | null): Promise<void> {
    await runOperation(async () => {
      const id = `${recordId}:${itemId}`
      if (value === null) {
        await requireRepository().removeActionFeedback(id)
        actionFeedback.value = actionFeedback.value.filter(entry => entry.id !== id)
      } else {
        const saved = await requireRepository().saveActionFeedback(recordId, itemId, value)
        actionFeedback.value = [...actionFeedback.value.filter(entry => entry.id !== id), saved]
      }
      feedbackRevision.value = Math.max(Date.now(), feedbackRevision.value + 1)
    })
  }

  async function setPetVisible(petVisible: boolean): Promise<void> {
    await savePreference(current => ({ ...preferenceFields(current), petVisible }))
  }

  async function confirmCoachStyle(coachStyleId: CoachStyleId): Promise<void> {
    await savePreference(current => ({ ...preferenceFields(current), coachStyleId }))
  }

  async function clearAllLocalData(): Promise<void> {
    await requireRepository().clearAllLocalData()
    resetLocalState(persistenceSuspended.value)
  }

  async function setSpeechReminders(enabled: boolean): Promise<void> {
    if (persistenceSuspended.value) throw new Error('local data persistence is suspended')
    forceSpeechOff = !enabled
    if (!enabled) applyPreferences(preferences.value)
    await savePreference(current => ({ ...preferenceFields(current),
      speechReminders: { automatic_speech: enabled, confirmed: true } }))
  }

  async function setCoachPosition(layout: 'mobile' | 'desktop', point: { x: number; y: number }): Promise<void> {
    if (![point.x, point.y].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) return
    await savePreference(current => ({ ...preferenceFields(current),
      coachPositions: { ...cloneJson(current.coachPositions ?? {}), [layout]: point } }))
  }

  async function quiescePersistence(): Promise<void> {
    persistenceSuspended.value = true
    await Promise.allSettled([...operations])
  }

  function resumePersistence(): void {
    persistenceSuspended.value = false
  }

  function resetLocalState(keepSuspended = false): void {
    plansRevision += 1
    plans.value = []
    records.value = []
    actionFeedback.value = []
    feedbackRevision.value = 0
    profile.value = emptyProfile()
    preferences.value = defaultPreferences()
    forceSpeechOff = false
    persistenceSuspended.value = keepSuspended
  }

  return {
    actionFeedback,
    feedbackRevision,
    saveActionFeedback,
    plans,
    records,
    profile,
    preferences,
    loaded,
    persistenceSuspended,
    preferencePending,
    load,
    reload,
    refreshHistory,
    saveCurrentDraftAs,
    openPlan,
    deletePlan,
    useQuickExperience,
    replaceCurrentDraft,
    saveProfile,
    clearProfile,
    setPetVisible,
    setSpeechReminders,
    setCoachPosition,
    confirmCoachStyle,
    clearAllLocalData,
    quiescePersistence,
    resumePersistence,
    resetLocalState,
  }
})
