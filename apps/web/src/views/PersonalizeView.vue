<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { AnalysisApiError, gymtiClient } from '@/api/client'
import type { CoachStyleId } from '@/domain/coach'
import type {
  GymtiAnswerRef,
  GymtiNarrativeSnapshot,
  PendingGymtiResult,
} from '@/domain/gymti'
import {
  deterministicLocalFallback,
  evaluateGymtiAnswers,
  GYMTI_QUESTIONNAIRE,
  legalNextQuestionCandidates,
  questionById,
  type GymtiQuestion,
} from '@/features/gymti'
import GymtiFlowHeader from '@/features/gymti/GymtiFlowHeader.vue'
import GymtiProfileStep from '@/features/gymti/GymtiProfileStep.vue'
import GymtiQuestionStep from '@/features/gymti/GymtiQuestionStep.vue'
import GymtiResultStep from '@/features/gymti/GymtiResultStep.vue'
import GymtiStylePicker from '@/features/gymti/GymtiStylePicker.vue'
import {
  COACH_STYLE_PRESENTATION,
  GYMTI_TYPE_PRESENTATION,
} from '@/features/gymti/presentation'
import type { GymtiQuestionViewModel } from '@/features/gymti/ui-types'
import { useGymtiStore } from '@/stores/gymti'
import { useLibraryStore } from '@/stores/library'

type FlowScreen = 'question' | 'profile' | 'result' | 'styles'

const route = useRoute()
const router = useRouter()
const gymti = useGymtiStore()
const library = useLibraryStore()

const screen = ref<FlowScreen>('question')
const initialized = ref(false)
const busy = ref(false)
const selectionFailure = ref(false)
const profileSaveFailed = ref(false)
const resultFailure = ref(false)
const notice = ref('')
const transitionLocked = ref(false)
const resultActivationPending = ref(false)
const failedSelectionAnswers = ref<GymtiAnswerRef[] | null>(null)
let selectionRevision = 0
let activeSelectionRevision: number | null = null

const contract = GYMTI_QUESTIONNAIRE
const originPath = computed(() => route.query.from === '/mine' ? '/mine' : '/plan')
const currentQuestion = computed<GymtiQuestion | null>(() => {
  const questionId = gymti.attempt?.currentQuestionId
  return questionId ? questionById(contract, questionId) : null
})
const questionViewModel = computed<GymtiQuestionViewModel | null>(() => {
  const question = currentQuestion.value
  return question ? {
    id: question.id,
    prompt: question.prompt,
    context: question.phase === 'terminal'
      ? '这题没有“都不像”；选一个更接近你的方向，答完就会得到结果。'
      : '选择更接近真实反应的一项，点击后会自动进入下一题。',
    options: question.options.map((option) => ({
      id: option.id,
      label: option.label,
    })),
  } : null
})
const answeredOptionId = computed(() => {
  const questionId = currentQuestion.value?.id
  return gymti.attempt?.answers.find((answer) => answer.questionId === questionId)?.optionId ?? null
})
const questionPosition = computed(() => {
  if (!gymti.attempt || !currentQuestion.value) return 1
  const answeredIndex = gymti.attempt.answers.findIndex(
    (answer) => answer.questionId === currentQuestion.value?.id,
  )
  return answeredIndex >= 0 ? answeredIndex + 1 : gymti.attempt.answers.length + 1
})
const headerLabel = computed(() => {
  if (screen.value === 'result') return ''
  if (screen.value === 'styles') return '修改风格'
  if (screen.value === 'profile') return '训练档案'
  return currentQuestion.value?.phase === 'terminal'
    ? '最后一题'
    : `第 ${questionPosition.value} 题`
})
const headerTitle = computed(() => screen.value === 'result' ? 'GYMTI · 测评结果' : 'GYMTI')
const resultForDisplay = computed(() => {
  const pending = gymti.pending
  if (pending?.narrative) {
    return {
      ...pending,
      narrative: pending.narrative,
    }
  }
  return gymti.current
})

const invalidateActiveSelection = (): void => {
  selectionRevision += 1
  if (activeSelectionRevision !== null) {
    activeSelectionRevision = null
    busy.value = false
  }
}

const settleSelection = (revision: number): void => {
  if (activeSelectionRevision !== revision) return
  activeSelectionRevision = null
  busy.value = false
  transitionLocked.value = false
}

const startAttempt = async (): Promise<void> => {
  if (transitionLocked.value) return
  invalidateActiveSelection()
  failedSelectionAnswers.value = null
  selectionFailure.value = false
  profileSaveFailed.value = false
  resultFailure.value = false
  notice.value = ''
  const firstQuestionId = contract.rules.foundationQuestionIds[0]
  if (!firstQuestionId) throw new Error('GYMTI contract does not define a first question')
  transitionLocked.value = true
  busy.value = true
  try {
    await gymti.beginAttempt({
      questionnaireVersion: contract.version,
      scoringVersion: contract.version,
      firstQuestionId,
      originPath: originPath.value,
    })
    screen.value = 'question'
  } finally {
    transitionLocked.value = false
    busy.value = false
  }
}

const exitFlow = async (): Promise<void> => {
  invalidateActiveSelection()
  failedSelectionAnswers.value = null
  await router.push(originPath.value)
}

const chooseNextQuestion = async (
  answers: GymtiAnswerRef[],
  revision: number,
): Promise<string | null> => {
  const candidates = legalNextQuestionCandidates(contract, answers)
  const candidateIds = candidates.map((question) => question.id)
  if (candidateIds.length === 0) return null
  if (candidateIds.length === 1) return candidateIds[0] ?? null

  try {
    const selected = await gymtiClient.chooseNextQuestion({
      questionnaireVersion: contract.version,
      scoringVersion: contract.version,
      answers,
      candidateQuestionIds: candidateIds,
    })
    if (revision !== selectionRevision) return null
    return candidateIds.includes(selected.questionId)
      ? selected.questionId
      : deterministicLocalFallback(candidateIds)
  } catch (error) {
    if (revision !== selectionRevision) return null
    if (error instanceof AnalysisApiError && error.status === 422) throw error
    return deterministicLocalFallback(candidateIds)
  }
}

const finishOrAdvance = async (
  answers: GymtiAnswerRef[],
  revision: number,
): Promise<void> => {
  const evaluation = evaluateGymtiAnswers(contract, answers)
  const terminalAnswered = answers.some(
    (answer) => questionById(contract, answer.questionId)?.phase === 'terminal',
  )

  if ((evaluation.earlyCompletion || terminalAnswered) && evaluation.formalResult) {
    if (revision !== selectionRevision) return
    transitionLocked.value = true
    await gymti.completeAttempt(evaluation.formalResult, answers)
    screen.value = 'profile'
    return
  }
  if (terminalAnswered) throw new Error('terminal GYMTI answer did not produce a result')

  const nextQuestionId = await chooseNextQuestion(
    answers,
    revision,
  )
  if (revision !== selectionRevision) return
  if (!nextQuestionId) throw new Error('GYMTI contract did not provide a legal next question')
  transitionLocked.value = true
  await gymti.updateProgress({ answers, currentQuestionId: nextQuestionId })
}

const selectOption = async (optionId: string): Promise<void> => {
  if (busy.value || !gymti.attempt || !currentQuestion.value) return
  const revision = ++selectionRevision
  activeSelectionRevision = revision
  busy.value = true
  selectionFailure.value = false
  transitionLocked.value = false
  const questionId = currentQuestion.value.id
  const existingIndex = gymti.attempt.answers.findIndex(
    (answer) => answer.questionId === questionId,
  )
  const prefix = existingIndex >= 0
    ? gymti.attempt.answers.slice(0, existingIndex)
    : gymti.attempt.answers
  const answers = [...prefix, { questionId, optionId }]
  failedSelectionAnswers.value = answers

  try {
    await finishOrAdvance(answers, revision)
    if (revision === selectionRevision) failedSelectionAnswers.value = null
  } catch {
    if (revision === selectionRevision) selectionFailure.value = true
  } finally {
    settleSelection(revision)
  }
}

const retrySelection = async (): Promise<void> => {
  if (busy.value || !gymti.attempt || !failedSelectionAnswers.value) return
  const revision = ++selectionRevision
  activeSelectionRevision = revision
  busy.value = true
  selectionFailure.value = false
  transitionLocked.value = false
  try {
    await finishOrAdvance([...failedSelectionAnswers.value], revision)
    if (revision === selectionRevision) failedSelectionAnswers.value = null
  } catch {
    if (revision === selectionRevision) selectionFailure.value = true
  } finally {
    settleSelection(revision)
  }
}

const backFromQuestion = async (): Promise<void> => {
  const attempt = gymti.attempt
  const question = currentQuestion.value
  if (!attempt || !question) return exitFlow()
  const currentIndex = attempt.answers.findIndex((answer) => answer.questionId === question.id)
  const previous = currentIndex >= 0
    ? attempt.answers[currentIndex - 1]
    : attempt.answers[attempt.answers.length - 1]
  if (!previous) return exitFlow()

  invalidateActiveSelection()
  failedSelectionAnswers.value = null
  selectionFailure.value = false
  transitionLocked.value = true
  busy.value = true
  try {
    await gymti.updateProgress({
      answers: [...attempt.answers],
      currentQuestionId: previous.questionId,
    })
  } finally {
    transitionLocked.value = false
    busy.value = false
  }
}

const handleBack = async (): Promise<void> => {
  if (transitionLocked.value) return
  if (screen.value === 'styles') {
    screen.value = 'result'
    return
  }
  if (screen.value === 'result') return exitFlow()
  if (screen.value === 'profile') {
    const lastAnswer = gymti.attempt?.answers.at(-1)
    if (!lastAnswer) return exitFlow()
    transitionLocked.value = true
    busy.value = true
    try {
      await gymti.reopenQuestionnaire(lastAnswer.questionId)
      failedSelectionAnswers.value = null
      screen.value = 'question'
      profileSaveFailed.value = false
      resultFailure.value = false
    } finally {
      transitionLocked.value = false
      busy.value = false
    }
    return
  }
  return backFromQuestion()
}

const localNarrative = (pending: PendingGymtiResult): GymtiNarrativeSnapshot => {
  const type = GYMTI_TYPE_PRESENTATION[pending.result.gymtiType]
  const style = COACH_STYLE_PRESENTATION[pending.result.recommendedCoachStyleId]
  return {
    text: `${type.shortDescription}${style.matchReason}`,
    source: 'template',
    version: 'gymti-narrative.v1',
    model: null,
    generatedAt: new Date().toISOString(),
  }
}

const openResult = async (): Promise<void> => {
  if (busy.value || !gymti.pending) return
  busy.value = true
  transitionLocked.value = true
  resultFailure.value = false
  try {
    await gymti.ensureNarrative(async (pending) => {
      try {
        return await gymtiClient.createNarrative({
          questionnaireVersion: pending.questionnaireVersion,
          scoringVersion: pending.scoringVersion,
          answers: pending.answers,
          formalResultId: pending.result.gymtiType,
          secondaryResultId: pending.result.secondaryGymtiType,
          coachStyleId: pending.result.recommendedCoachStyleId,
          reasonCodes: pending.result.reasonCodes.slice(0, 3),
        })
      } catch {
        return localNarrative(pending)
      }
    })
    screen.value = 'result'
  } catch {
    resultFailure.value = true
  } finally {
    busy.value = false
    transitionLocked.value = false
  }
}

const activateRenderedResult = async (): Promise<void> => {
  const pendingResultId = gymti.pending?.narrative ? gymti.pending.resultId : null
  if (!pendingResultId || screen.value !== 'result' || resultActivationPending.value) return

  resultActivationPending.value = true
  transitionLocked.value = true
  busy.value = true
  try {
    if (gymti.pending?.resultId !== pendingResultId || screen.value !== 'result') return
    await gymti.presentPendingResult()
    resultFailure.value = false
  } catch {
    if (gymti.pending?.resultId === pendingResultId) {
      resultFailure.value = true
      screen.value = 'profile'
    }
  } finally {
    resultActivationPending.value = false
    transitionLocked.value = false
    busy.value = false
  }
}

const saveProfile = async (profile: Parameters<typeof library.saveProfile>[0]): Promise<void> => {
  if (busy.value) return
  busy.value = true
  transitionLocked.value = true
  profileSaveFailed.value = false
  try {
    await library.saveProfile(profile)
  } catch {
    profileSaveFailed.value = true
    busy.value = false
    transitionLocked.value = false
    return
  }
  busy.value = false
  transitionLocked.value = false
  await openResult()
}

const clearProfile = async (): Promise<void> => {
  if (!window.confirm('清除当前设备上已有的训练档案？')) return
  busy.value = true
  transitionLocked.value = true
  try {
    await library.clearProfile()
    profileSaveFailed.value = false
  } catch {
    profileSaveFailed.value = true
  } finally {
    busy.value = false
    transitionLocked.value = false
  }
}

const confirmStyle = async (styleId: CoachStyleId): Promise<void> => {
  if (busy.value) return
  busy.value = true
  notice.value = ''
  try {
    await library.confirmCoachStyle(styleId)
    await router.push(originPath.value)
  } catch {
    notice.value = '教练风格没有保存成功，请重试。'
  } finally {
    busy.value = false
  }
}

const confirmRecommendation = async (): Promise<void> => {
  const styleId = gymti.current?.result.recommendedCoachStyleId
  if (styleId) await confirmStyle(styleId)
}

watch(
  [initialized, screen, () => currentQuestion.value?.id],
  async () => {
    if (!initialized.value) return
    await nextTick()
    const heading = document.querySelector<HTMLElement>('.gymti-page h1')
    if (!heading) return
    heading.tabIndex = -1
    heading.focus({ preventScroll: true })
  },
  { flush: 'post' },
)

onMounted(async () => {
  try {
    if (route.query.retest === '1') {
      await startAttempt()
    } else if (gymti.pending && gymti.attempt?.phase === 'profile') {
      screen.value = 'profile'
    } else if (gymti.attempt) {
      screen.value = gymti.attempt.phase === 'profile' ? 'profile' : 'question'
    } else if (route.query.view === 'styles' && gymti.current) {
      screen.value = 'styles'
    } else if (gymti.current) {
      screen.value = 'result'
    } else {
      await startAttempt()
    }
  } catch {
    notice.value = 'GYMTI 暂时无法在当前设备上开始，请返回后重试。'
  } finally {
    initialized.value = true
  }
})
</script>

<template>
  <main class="gymti-page tp-page tp-page--immersive">
    <GymtiFlowHeader
      :title="headerTitle"
      :label="headerLabel"
      :back-label="screen === 'question' && questionPosition === 1 ? '退出测评' : '返回上一步'"
      :locked="transitionLocked"
      @back="handleBack"
    />

    <p v-if="notice" class="flow-notice" role="alert">{{ notice }}</p>

    <section v-if="!initialized" class="flow-loading" aria-live="polite">
      <p class="tp-kicker">GYMTI</p>
      <h1>正在翻到第一题</h1>
    </section>

    <GymtiQuestionStep
      v-else-if="screen === 'question' && questionViewModel"
      :key="questionViewModel.id"
      :question="questionViewModel"
      :answered-option-id="answeredOptionId"
      :busy="busy"
      :failure="selectionFailure"
      @select="selectOption"
      @retry="retrySelection"
      @exit="exitFlow"
    />

    <template v-else-if="screen === 'profile'">
      <div v-if="resultFailure" class="result-failure" role="alert">
        <strong>结果暂时没有打开</strong>
        <p>问卷答案和待展示结果仍保存在当前设备。</p>
        <button type="button" :disabled="busy" @click="openResult">重试打开结果</button>
      </div>
      <GymtiProfileStep
        :profile="library.profile"
        :busy="busy"
        :save-failed="profileSaveFailed"
        @save="saveProfile"
        @skip="openResult"
        @clear="clearProfile"
      />
    </template>

    <GymtiResultStep
      v-else-if="screen === 'result' && resultForDisplay"
      :current="resultForDisplay"
      :confirmed-style-id="library.preferences.coachStyleId"
      :busy="busy"
      @confirm="confirmRecommendation"
      @modify="screen = 'styles'"
      @retest="startAttempt"
      @ready="activateRenderedResult"
    />

    <GymtiStylePicker
      v-else-if="screen === 'styles' && gymti.current"
      :recommended-style-id="gymti.current.result.recommendedCoachStyleId"
      :confirmed-style-id="library.preferences.coachStyleId"
      :busy="busy"
      @confirm="confirmStyle"
    />

    <section v-else class="flow-unavailable" role="alert">
      <h1>这一页暂时没有准备好</h1>
      <p>已保存的数据没有被清除，可以返回后重新进入。</p>
      <button type="button" @click="exitFlow">返回</button>
    </section>
  </main>
</template>

<style scoped>
.gymti-page { display: grid; align-content: start; gap: 18px; max-width: 760px; min-height: 100dvh; padding-bottom: calc(32px + var(--tp-task-reserve, 0px)); }
.gymti-page :deep(h1:focus) { outline: none; }
.flow-notice { margin: 0; padding: 12px 14px; border-left: 3px solid var(--tp-danger); border-radius: 0 12px 12px 0; color: var(--tp-danger); background: rgb(179 38 30 / 6%); font-size: 12px; line-height: 1.6; }
.flow-loading,
.flow-unavailable { display: grid; min-height: 58dvh; place-content: center; justify-items: center; gap: 12px; text-align: center; }
.flow-loading h1,
.flow-unavailable h1 { max-width: 420px; margin: 0; font-size: clamp(34px, 11vw, 54px); line-height: 1; }
.flow-unavailable p { max-width: 340px; margin: 0; color: var(--tp-muted); font-size: 13px; line-height: 1.65; }
.flow-unavailable button,
.result-failure button { min-height: 44px; padding: 0 16px; border: 1px solid var(--tp-line); border-radius: 999px; color: var(--tp-ink); background: var(--tp-surface); font-weight: 800; }
.result-failure { display: grid; justify-items: start; gap: 6px; padding: 14px; border: 1px solid rgb(179 38 30 / 22%); border-radius: 15px; color: var(--tp-danger); background: rgb(179 38 30 / 5%); }
.result-failure p { margin: 0; color: var(--tp-muted); font-size: 11px; line-height: 1.55; }
.result-failure button { margin-top: 4px; }
</style>
