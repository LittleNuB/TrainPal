import { ref } from 'vue'
import { defineStore } from 'pinia'

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
  const plans = ref<SavedPlan[]>([])
  const records = ref<TrainingRecord[]>([])
  const profile = ref<TrainingProfile>(emptyProfile())
  const preferences = ref<Preferences>(defaultPreferences())
  const loaded = ref(false)
  const persistenceSuspended = ref(false)
  let repository: LibraryRepository | null = null
  let plansRevision = 0
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

  async function load(nextRepository: LibraryRepository): Promise<void> {
    repository = nextRepository
    const [savedPlans, savedRecords, savedProfile, savedPreferences] = await Promise.all([
      repository.listPlans(),
      repository.listRecords(),
      repository.loadProfile(),
      repository.loadPreferences(),
    ])
    plans.value = savedPlans
    records.value = savedRecords
    profile.value = savedProfile ?? emptyProfile()
    preferences.value = savedPreferences ?? defaultPreferences()
    loaded.value = true
  }

  async function reload(): Promise<void> {
    const currentRepository = requireRepository()
    const expectedPlansRevision = plansRevision
    const [savedPlans, savedRecords, savedProfile, savedPreferences] = await Promise.all([
      currentRepository.listPlans(),
      currentRepository.listRecords(),
      currentRepository.loadProfile(),
      currentRepository.loadPreferences(),
    ])
    if (plansRevision === expectedPlansRevision) plans.value = savedPlans
    records.value = savedRecords
    profile.value = savedProfile ?? emptyProfile()
    preferences.value = savedPreferences ?? defaultPreferences()
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
      profile.value = await requireRepository().saveProfile(input)
    })
  }

  async function clearProfile(): Promise<void> {
    await saveProfile({ sex: null, age: null, heightCm: null, weightKg: null })
  }

  async function setPetVisible(petVisible: boolean): Promise<void> {
    await runOperation(async () => {
      preferences.value = await requireRepository().savePreferences({
        petVisible,
        coachStyleId: preferences.value.coachStyleId,
        ...(preferences.value.coachPositions ? { coachPositions: cloneJson(preferences.value.coachPositions) } : {}),
      })
    })
  }

  async function confirmCoachStyle(coachStyleId: CoachStyleId): Promise<void> {
    await runOperation(async () => {
      preferences.value = await requireRepository().savePreferences({
        petVisible: preferences.value.petVisible,
        coachStyleId,
        ...(preferences.value.coachPositions ? { coachPositions: cloneJson(preferences.value.coachPositions) } : {}),
      })
    })
  }

  async function clearAllLocalData(): Promise<void> {
    await requireRepository().clearAllLocalData()
    resetLocalState(persistenceSuspended.value)
  }

  async function setCoachPosition(layout: 'mobile' | 'desktop', point: { x: number; y: number }): Promise<void> {
    if (![point.x, point.y].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) return
    await runOperation(async () => {
      preferences.value = await requireRepository().savePreferences({
        petVisible: preferences.value.petVisible,
        coachStyleId: preferences.value.coachStyleId,
        coachPositions: { ...cloneJson(preferences.value.coachPositions ?? {}), [layout]: point },
      })
    })
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
    profile.value = emptyProfile()
    preferences.value = defaultPreferences()
    persistenceSuspended.value = keepSuspended
  }

  return {
    plans,
    records,
    profile,
    preferences,
    loaded,
    persistenceSuspended,
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
    setCoachPosition,
    confirmCoachStyle,
    clearAllLocalData,
    quiescePersistence,
    resumePersistence,
    resetLocalState,
  }
})
