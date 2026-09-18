import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan } from '@/domain/types'
import { useLibraryStore } from '@/stores/library'

const currentDraft: DraftPlan = {
  id: 'current',
  name: '8 分钟手臂唤醒',
  linkedPlanId: null,
  items: [],
  updatedAt: '2026-07-21T00:00:00.000Z',
}

const repository = (): LibraryRepository => ({
  listPlans: vi.fn().mockResolvedValue([]),
  listRecords: vi.fn().mockResolvedValue([]),
  loadProfile: vi.fn().mockResolvedValue(null),
  loadPreferences: vi.fn().mockResolvedValue(null),
  saveProfile: vi.fn().mockImplementation(async (profile) => ({
    ...profile,
    id: 'current',
    updatedAt: '2026-07-21T00:00:00.000Z',
  })),
  savePreferences: vi.fn().mockImplementation(async (preferences) => ({
    ...preferences,
    id: 'current',
    updatedAt: '2026-07-21T00:00:00.000Z',
  })),
  saveCurrentDraftAs: vi.fn(),
  openPlan: vi.fn(),
  deletePlan: vi.fn().mockResolvedValue(null),
  replaceCurrentDraft: vi.fn().mockResolvedValue(currentDraft),
  clearAllLocalData: vi.fn().mockResolvedValue(undefined),
})

beforeEach(() => setActivePinia(createPinia()))

describe('local training library store', () => {
  it('remembers separate coach positions through visibility and style changes', async () => {
    const persistence = repository()
    const store = useLibraryStore()
    await store.load(persistence)
    await store.setCoachPosition('mobile', { x: 1, y: .4 })
    await store.setCoachPosition('desktop', { x: .7, y: .6 })
    await store.setPetVisible(false)
    await store.confirmCoachStyle('gentle')
    expect(store.preferences.coachPositions).toEqual({ mobile: { x: 1, y: .4 }, desktop: { x: .7, y: .6 } })
    expect(store.preferences.petVisible).toBe(false)
    expect(store.preferences.coachStyleId).toBe('gentle')
    const calls = vi.mocked(persistence.savePreferences).mock.calls.length
    await store.setCoachPosition('mobile', { x: Number.NaN, y: 2 })
    expect(persistence.savePreferences).toHaveBeenCalledTimes(calls)
    await store.clearAllLocalData()
    expect(store.preferences.coachPositions).toBeUndefined()
  })

  it('defaults Pet to visible and persists an explicit hidden preference', async () => {
    const persistence = repository()
    const store = useLibraryStore()
    await store.load(persistence)

    expect(store.preferences.petVisible).toBe(true)
    expect(store.preferences.coachStyleId).toBeNull()
    await store.setPetVisible(false)
    expect(store.preferences.petVisible).toBe(false)
    expect(persistence.savePreferences).toHaveBeenCalledWith({
      petVisible: false,
      coachStyleId: null,
    })
  })

  it('persists a coach style only after an explicit confirmation', async () => {
    const persistence = repository()
    const store = useLibraryStore()
    await store.load(persistence)

    expect(store.preferences.coachStyleId).toBeNull()
    await store.confirmCoachStyle('gentle')

    expect(store.preferences.coachStyleId).toBe('gentle')
    expect(persistence.savePreferences).toHaveBeenCalledWith({
      petVisible: true,
      coachStyleId: 'gentle',
    })
  })

  it('installs the labelled quick experience as an unlinked current draft', async () => {
    const persistence = repository()
    const store = useLibraryStore()
    await store.load(persistence)

    const draft = await store.useQuickExperience()
    expect(draft.name).toBe('8 分钟手臂唤醒')
    expect(draft.linkedPlanId).toBeNull()
    expect(persistence.replaceCurrentDraft).toHaveBeenCalledTimes(1)
  })

  it('quiesces an in-flight preference write and blocks later writes until resumed', async () => {
    let releasePreference!: () => void
    const persistence = repository()
    vi.mocked(persistence.savePreferences).mockImplementation(async (preferences) => {
      await new Promise<void>((resolve) => { releasePreference = resolve })
      return {
        ...preferences,
        id: 'current',
        updatedAt: '2026-07-21T00:00:00.000Z',
      }
    })
    const store = useLibraryStore()
    await store.load(persistence)
    const saving = store.setPetVisible(false)
    let quiesced = false
    const quiescing = store.quiescePersistence().then(() => { quiesced = true })

    await Promise.resolve()
    expect(quiesced).toBe(false)
    await expect(store.setPetVisible(false)).rejects.toThrow(/suspended/)

    releasePreference()
    await saving
    await quiescing
    store.resetLocalState(true)

    expect(store.preferences.petVisible).toBe(true)
    expect(persistence.savePreferences).toHaveBeenCalledTimes(1)
  })
})
