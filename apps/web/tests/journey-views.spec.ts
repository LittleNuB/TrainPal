import { flushPromises, mount, type Stubs } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { gymtiClient, type GymtiNextQuestionChoice } from '@/api/client'
import type { LibraryRepository } from '@/db/library-repository'
import type { GymtiLifecycleState, GymtiRepository } from '@/db/gymti-repository'
import type {
  CurrentGymtiResult,
  GymtiAttempt,
  PendingGymtiResult,
} from '@/domain/gymti'
import type { DraftPlan } from '@/domain/types'
import type { Preferences, TrainingProfile } from '@/domain/training'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useGymtiStore } from '@/stores/gymti'
import PersonalizeView from '@/views/PersonalizeView.vue'
import TrainHubView from '@/views/TrainHubView.vue'

const emptyLibrary = (): LibraryRepository => ({
  listActionFeedback: async () => [],  saveActionFeedback: async () => { throw new Error('not used') },  removeActionFeedback: async () => {},  listPlans: async () => [],
  listRecords: async () => [],
  loadProfile: async () => null,
  loadPreferences: async () => null,
  saveProfile: async (profile: Omit<TrainingProfile, 'id' | 'updatedAt'>) => ({
    ...profile,
    id: 'current',
    updatedAt: new Date(0).toISOString(),
  }),
  savePreferences: async (preferences: Omit<Preferences, 'id' | 'updatedAt'>) => ({
    ...preferences,
    id: 'current',
    updatedAt: new Date(0).toISOString(),
  }),
  saveCurrentDraftAs: async () => { throw new Error('not used') },
  openPlan: async () => { throw new Error('not used') },
  deletePlan: async () => null,
  replaceCurrentDraft: async ({ name, items }): Promise<DraftPlan> => ({
    id: 'current',
    name,
    linkedPlanId: null,
    items: structuredClone(items),
    updatedAt: new Date(0).toISOString(),
  }),
  clearAllLocalData: async () => undefined,
})

const emptyGymti = (state: GymtiLifecycleState = {
  attempt: null,
  pending: null,
  current: null,
}): GymtiRepository => ({
  loadState: async () => structuredClone(state),
  startAttempt: async () => undefined,
  saveAttempt: async () => undefined,
  discardAttempt: async () => undefined,
  saveCompletedAttempt: async () => undefined,
  reopenAttempt: async () => undefined,
  savePendingResult: async () => undefined,
  attachNarrative: async () => { throw new Error('not used') },
  activatePendingResult: async () => { throw new Error('not used') },
})

const mountAt = async (
  component: typeof PersonalizeView | typeof TrainHubView,
  path: string,
  gymtiState?: GymtiLifecycleState,
  options?: {
    gymtiRepository?: GymtiRepository
    stubs?: Stubs
  },
) => {
  const pinia = createPinia()
  setActivePinia(pinia)
  await useDraftStore().load({ load: async () => undefined, save: async () => undefined })
  await useLibraryStore().load(emptyLibrary())
  await useGymtiStore().load(options?.gymtiRepository ?? emptyGymti(gymtiState))
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/personalize', component: PersonalizeView },
      { path: '/train', component: TrainHubView },
      { path: '/plan', component: { template: '<p>plan</p>' } },
      { path: '/mine', component: { template: '<p>mine</p>' } },
      { path: '/', component: { template: '<p>home</p>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return mount(component, {
    global: {
      plugins: [pinia, router],
      stubs: options?.stubs,
    },
  })
}

describe('TrainPal 旅程页面', () => {
  it('starts the real GYMTI flow, auto-advances, and lets the user return to the prior answer', async () => {
    const wrapper = await mountAt(PersonalizeView, '/personalize')
    await flushPromises()

    expect(wrapper.text()).toContain('今天本来计划训练')
    expect(wrapper.text()).toContain('第 1 题')
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('继续')

    await wrapper.get('[data-option-id="q01_b_small_win"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('健身房里你最容易被什么吸引')
    expect(wrapper.text()).toContain('第 2 题')

    await wrapper.get('.gymti-flow-header button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('今天本来计划训练')
    expect(wrapper.get('[data-option-id="q01_b_small_win"]').attributes('aria-checked')).toBe('true')
  })

  it('resumes an interrupted retest before showing the previous current result', async () => {
    const timestamp = '2026-07-23T00:00:00.000Z'
    const attempt: GymtiAttempt = {
      id: 'current',
      attemptId: 'retest-attempt',
      questionnaireVersion: 'gymti-questionnaire.v1',
      scoringVersion: 'gymti-questionnaire.v1',
      answers: [],
      currentQuestionId: 'q01_energy_after_work',
      phase: 'questionnaire',
      originPath: '/plan',
      startedAt: timestamp,
      updatedAt: timestamp,
    }
    const current: CurrentGymtiResult = {
      id: 'current',
      resultId: 'previous-result',
      attemptId: 'previous-attempt',
      questionnaireVersion: 'gymti-questionnaire.v1',
      scoringVersion: 'gymti-questionnaire.v1',
      answers: [{ questionId: 'q01_energy_after_work', optionId: 'q01_b_small_win' }],
      result: {
        gymtiType: 'LIFE',
        secondaryGymtiType: null,
        recommendedCoachStyleId: 'gentle',
        reasonCodes: ['goal_vitality'],
        excludedCoachStyleIds: [],
      },
      narrative: {
        text: '先恢复电量，再慢慢开始。',
        source: 'template',
        version: 'gymti-questionnaire.v1',
        model: null,
        generatedAt: timestamp,
      },
      createdAt: timestamp,
      updatedAt: timestamp,
      activatedAt: timestamp,
    }

    const wrapper = await mountAt(PersonalizeView, '/personalize', {
      attempt,
      pending: null,
      current,
    })
    await flushPromises()

    expect(wrapper.text()).toContain('今天本来计划训练')
    expect(wrapper.text()).not.toContain('GYMTI · 测评结果')
  })

  it('does not activate a pending result until the result child reports a successful mount', async () => {
    const timestamp = '2026-07-23T00:00:00.000Z'
    const answers = [{ questionId: 'q01_energy_after_work', optionId: 'q01_b_small_win' }]
    const attempt: GymtiAttempt = {
      id: 'current',
      attemptId: 'attempt-pending',
      questionnaireVersion: 'gymti-questionnaire.v1',
      scoringVersion: 'gymti-questionnaire.v1',
      answers,
      currentQuestionId: 'q01_energy_after_work',
      phase: 'profile',
      originPath: '/plan',
      startedAt: timestamp,
      updatedAt: timestamp,
    }
    const pending: PendingGymtiResult = {
      id: 'current',
      resultId: 'result-pending',
      attemptId: attempt.attemptId,
      questionnaireVersion: attempt.questionnaireVersion,
      scoringVersion: attempt.scoringVersion,
      answers,
      result: {
        gymtiType: 'LIFE',
        secondaryGymtiType: null,
        recommendedCoachStyleId: 'gentle',
        reasonCodes: ['goal_vitality'],
        excludedCoachStyleIds: [],
      },
      narrative: {
        text: '先恢复电量，再慢慢开始。',
        source: 'template',
        version: 'gymti-narrative.v1',
        model: null,
        generatedAt: timestamp,
      },
      createdAt: timestamp,
      updatedAt: timestamp,
    }
    const state = { attempt, pending, current: null }
    const persistence = emptyGymti(state)
    const activate = vi.fn()
    persistence.activatePendingResult = activate

    const wrapper = await mountAt(PersonalizeView, '/personalize', state, {
      gymtiRepository: persistence,
      stubs: {
        GymtiResultStep: {
          template: '<div data-result-render-incomplete></div>',
        },
      },
    })
    await flushPromises()
    await wrapper.get('[data-action="skip"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-result-render-incomplete]').exists()).toBe(true)
    expect(activate).not.toHaveBeenCalled()
  })

  it('keeps a newer persistence lock when an invalidated adaptive request settles late', async () => {
    const timestamp = '2026-07-23T00:00:00.000Z'
    const answers = [
      { questionId: 'q01_energy_after_work', optionId: 'q01_c_music' },
      { questionId: 'q02_gym_attraction', optionId: 'q02_b_cardio' },
      { questionId: 'q03_data_report', optionId: 'q03_b_skip_math' },
      { questionId: 'q04_desktop_cat', optionId: 'q04_d_tease' },
    ]
    const attempt: GymtiAttempt = {
      id: 'current',
      attemptId: 'attempt-race',
      questionnaireVersion: 'gymti-questionnaire.v1',
      scoringVersion: 'gymti-questionnaire.v1',
      answers,
      currentQuestionId: 'q05_reminder_aversion',
      phase: 'questionnaire',
      originPath: '/plan',
      startedAt: timestamp,
      updatedAt: timestamp,
    }
    const state = { attempt, pending: null, current: null }
    const persistence = emptyGymti(state)
    let releaseSecondSave = (): void => undefined
    const secondSave = new Promise<void>((resolve) => { releaseSecondSave = resolve })
    let saveCount = 0
    persistence.saveAttempt = vi.fn(async () => {
      saveCount += 1
      if (saveCount === 2) await secondSave
    })
    let resolveFirstChoice = (_choice: GymtiNextQuestionChoice): void => undefined
    const firstChoice = new Promise<GymtiNextQuestionChoice>((resolve) => {
      resolveFirstChoice = resolve
    })
    const chooseQuestion = vi.spyOn(gymtiClient, 'chooseNextQuestion')
      .mockImplementationOnce(async () => firstChoice)
      .mockImplementation(async (input) => ({
        questionId: input.candidateQuestionIds[0]!,
        source: 'local_fallback',
        model: null,
        version: input.questionnaireVersion,
      }))

    try {
      const wrapper = await mountAt(PersonalizeView, '/personalize', state, {
        gymtiRepository: persistence,
      })
      await flushPromises()
      await wrapper.get('[data-option-id="q05_b_self_judgment"]').trigger('click')
      await flushPromises()
      expect(chooseQuestion).toHaveBeenCalledTimes(1)

      await wrapper.get('.gymti-flow-header button').trigger('click')
      await flushPromises()
      expect(wrapper.find('[data-option-id="q04_d_tease"]').exists()).toBe(true)
      expect(wrapper.get('[data-option-id="q04_d_tease"]').attributes('disabled')).toBeUndefined()

      await wrapper.get('[data-option-id="q04_d_tease"]').trigger('click')
      await flushPromises()
      expect(saveCount).toBe(2)
      expect(wrapper.get('.gymti-flow-header button').attributes('disabled')).toBeDefined()

      resolveFirstChoice({
        questionId: 'q07_intensity_view',
        source: 'llm',
        model: 'test-model',
        version: 'gymti-questionnaire.v1',
      })
      await flushPromises()
      expect(wrapper.get('.gymti-flow-header button').attributes('disabled')).toBeDefined()
      expect(wrapper.get('[data-option-id="q04_d_tease"]').attributes('disabled')).toBeDefined()

      releaseSecondSave()
      await flushPromises()
      expect(wrapper.get('.gymti-flow-header button').attributes('disabled')).toBeUndefined()
    } finally {
      chooseQuestion.mockRestore()
      releaseSecondSave()
    }
  })

  it('keeps the training hub focused on the next training action without duplicating records', async () => {
    const wrapper = await mountAt(TrainHubView, '/train')

    expect(wrapper.text()).toContain('先准备一场想练的训练')
    expect(wrapper.text()).not.toContain('训练记录')
    expect(wrapper.findAll('.tp-primary-action')).toHaveLength(1)
  })
})
