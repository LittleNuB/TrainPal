import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { LibraryRepository } from '@/db/library-repository'
import type { DraftPlan, DraftRepository } from '@/domain/types'
import type { Preferences, SavedPlan, TrainingProfile, TrainingRecord } from '@/domain/training'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import {
  LocalDataCoordinationUnavailableError,
  type LocalDataClearCoordinator,
  type LocalDataClearParticipant,
} from '@/local-data/clear-coordinator'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useLocalDataClearStore } from '@/stores/local-data-clear'
import MineView from '@/views/MineView.vue'

const trainingRecord = (): TrainingRecord => ({
  id: 'record-1',
  outcome: 'completed',
  plan: {
    name: '手臂训练',
    source: 'draft',
    sourcePlanId: null,
    items: createQuickExperienceDraftItems(),
  },
  actions: [{
    itemId: 'quick-curl',
    name: '弯举',
    targetSets: 3,
    completedSets: 3,
    completedReps: 30,
    completedDurationSeconds: null,
    activeSeconds: 90,
    status: 'completed',
  }],
  activeSeconds: 90,
  creditedRestSeconds: 60,
  trainingDurationSeconds: 150,
  completedActionCount: 1,
  calorie: { value: 12, method: 'generic' },
  petId: 'hachimi',
  coachStyleId: null,
  startedAt: '2026-07-21T00:00:00.000Z',
  endedAt: '2026-07-21T00:03:00.000Z',
})

class MemoryLibraryRepository implements LibraryRepository {
  plans: SavedPlan[] = []
  records: TrainingRecord[] = []
  profile: TrainingProfile | null = null
  preferences: Preferences | null = null
  clearCalls = 0
  failPreferences = false

  async listActionFeedback() { return [] }
  async saveActionFeedback(): Promise<never> { throw new Error('not used') }
  async removeActionFeedback() {}
  async listPlans() { return structuredClone(this.plans) }
  async listRecords() { return structuredClone(this.records) }
  async loadProfile() { return structuredClone(this.profile) }
  async loadPreferences() { return structuredClone(this.preferences) }
  async saveProfile(profile: Omit<TrainingProfile, 'id' | 'updatedAt'>) {
    this.profile = { ...profile, id: 'current', updatedAt: new Date(0).toISOString() }
    return structuredClone(this.profile)
  }
  async savePreferences(preferences: Omit<Preferences, 'id' | 'updatedAt'>) {
    if (this.failPreferences) throw new Error('indexeddb write failed')
    this.preferences = { ...preferences, id: 'current', updatedAt: new Date(0).toISOString() }
    return structuredClone(this.preferences)
  }
  async saveCurrentDraftAs(): Promise<never> { throw new Error('not used') }
  async openPlan(): Promise<never> { throw new Error('not used') }
  async deletePlan(): Promise<never> { throw new Error('not used') }
  async replaceCurrentDraft(): Promise<never> { throw new Error('not used') }
  async clearAllLocalData() { this.clearCalls += 1 }
}

class DeferredDraftRepository implements DraftRepository {
  saveStarted!: () => void
  releaseSave!: () => void
  readonly started = new Promise<void>((resolve) => { this.saveStarted = resolve })
  private readonly released = new Promise<void>((resolve) => { this.releaseSave = resolve })

  async load(): Promise<DraftPlan | undefined> { return undefined }
  async save(): Promise<void> {
    this.saveStarted()
    await this.released
  }
}

class InlineClearCoordinator implements LocalDataClearCoordinator {
  readonly supported = true
  private participant: LocalDataClearParticipant | null = null

  connect(participant: LocalDataClearParticipant): void { this.participant = participant }
  async clear(clearDatabase: () => Promise<void>): Promise<void> {
    if (!this.participant) throw new Error('not connected')
    await this.participant.prepare('epoch-2')
    try {
      await clearDatabase()
      await this.participant.commit('epoch-2')
    } catch (error) {
      await this.participant.abort('epoch-2')
      throw error
    }
  }
  close(): void {}
}

class UnsupportedClearCoordinator implements LocalDataClearCoordinator {
  readonly supported = false
  connect(): void {}
  async clear(): Promise<void> { throw new LocalDataCoordinationUnavailableError() }
  close(): void {}
}

const mountMine = async (pinia: ReturnType<typeof createPinia>, attachTo?: HTMLElement) => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/mine', component: MineView },
      { path: '/personalize', component: { template: '<p>personalize</p>' } },
      { path: '/result/:recordId', component: { template: '<p>result</p>' } },
    ],
  })
  await router.push('/mine')
  await router.isReady()
  return {
    router,
    wrapper: mount(MineView, {
      ...(attachTo ? { attachTo } : {}),
      global: { plugins: [pinia, router] },
    }),
  }
}

describe('我的', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('offers the real GYMTI entry without inventing an unconfirmed coach', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useLibraryStore().load(new MemoryLibraryRepository())
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    expect(wrapper.text()).toContain('GYMTI 健身目标')
    expect(wrapper.text()).toContain('开始测评')
    expect(wrapper.text()).toContain('测评后确认小猫')
    expect(wrapper.text()).toContain('尚未确认')
    expect(wrapper.text()).not.toContain('哈肌咪')
  })

  it('counts effective training days from records with actual completed sets', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new MemoryLibraryRepository()
    repository.records = [trainingRecord(), {
      ...trainingRecord(),
      id: 'record-empty',
      actions: [],
      completedActionCount: 0,
    }]
    await useLibraryStore().load(repository)
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    expect(wrapper.get('.growth-overview').text()).toContain('1 个有效训练日')
  })

  it('opens records on demand and keeps the main page compact', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new MemoryLibraryRepository()
    repository.records = [trainingRecord()]
    await useLibraryStore().load(repository)
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    expect(wrapper.find('.detail-sheet').exists()).toBe(false)
    await wrapper.findAll('.setting-row')[3]!.trigger('click')

    expect(wrapper.get('.detail-sheet').text()).toContain('手臂训练')
    expect(wrapper.get('.detail-sheet').text()).toContain('约 12 千卡')
  })

  it('moves, traps, and restores keyboard focus for a detail sheet', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    await useLibraryStore().load(new MemoryLibraryRepository())
    const host = document.createElement('div')
    document.body.append(host)
    const { wrapper } = await mountMine(pinia, host)
    await flushPromises()

    const trigger = wrapper.findAll('.setting-row')[2]!.element as HTMLButtonElement
    trigger.focus()
    await wrapper.findAll('.setting-row')[2]!.trigger('click')
    await flushPromises()

    const sheet = wrapper.get('.detail-sheet')
    const close = sheet.get('[data-dialog-initial-focus]').element as HTMLButtonElement
    expect(document.activeElement).toBe(close)

    const focusable = sheet.element.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled])',
    )
    const last = focusable[focusable.length - 1]!
    last.focus()
    last.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(close)

    close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.detail-sheet').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger)

    wrapper.unmount()
    host.remove()
  })

  it('saves optional profile fields without presenting them as a required intensity input', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new MemoryLibraryRepository()
    await useLibraryStore().load(repository)
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    await wrapper.findAll('.setting-row')[2]!.trigger('click')
    await wrapper.get('input[aria-label="年龄"]').setValue('28')
    await wrapper.get('.profile-form').trigger('submit')
    await flushPromises()

    expect(repository.profile?.age).toBe(28)
    expect(wrapper.get('.notice').text()).toBe('训练档案已保存到本机')
    expect(wrapper.get('.profile-panel').text()).toContain('都可以留空')
  })

  it('shows a safe notice when the TrainPal visibility preference cannot be saved', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new MemoryLibraryRepository()
    await useLibraryStore().load(repository)
    repository.failPreferences = true
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    await wrapper.findAll('.setting-row')[1]!.trigger('click')
    await wrapper.get('.preference-row button').trigger('click')
    await flushPromises()

    expect(wrapper.get('.notice').text()).toContain('显示偏好没有保存成功')
    expect(wrapper.text()).not.toContain('indexeddb write failed')
  })

  it('waits for pending persistence before clearing all local data', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    setActivePinia(pinia)
    const draftRepository = new DeferredDraftRepository()
    const draft = useDraftStore()
    await draft.load(draftRepository)
    draft.addManualAction({ name: '平板支撑', mode: 'duration' })
    await vi.advanceTimersByTimeAsync(301)
    await draftRepository.started
    const repository = new MemoryLibraryRepository()
    await useLibraryStore().load(repository)
    useLocalDataClearStore().initialize(new InlineClearCoordinator())
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper } = await mountMine(pinia)

    await wrapper.findAll('.setting-row')[4]!.trigger('click')
    const clear = wrapper.get('button.clear-data').trigger('click')
    await Promise.resolve()
    expect(repository.clearCalls).toBe(0)

    draftRepository.releaseSave()
    await clear
    await flushPromises()
    expect(repository.clearCalls).toBe(1)
    expect(draft.items).toHaveLength(0)
    expect(wrapper.get('.notice').text()).toBe('本机视频、问卷和训练数据已清除')
  })

  it('does not claim data was cleared when safe cross-tab coordination is unavailable', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const repository = new MemoryLibraryRepository()
    await useLibraryStore().load(repository)
    useLocalDataClearStore().initialize(new UnsupportedClearCoordinator())
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper } = await mountMine(pinia)
    await flushPromises()

    await wrapper.findAll('.setting-row')[4]!.trigger('click')
    await wrapper.get('button.clear-data').trigger('click')
    await flushPromises()

    expect(repository.clearCalls).toBe(0)
    expect(wrapper.get('.notice').text()).toContain('数据没有清除')
  })
})
