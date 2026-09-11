<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useDialogFocus } from '@/composables/useDialogFocus'
import { fingerprintMatches, probeVideoDuration, SUPPORTED_LOCAL_MEDIA_TYPES } from '@/domain/local-media'
import { estimatePlanMinutes } from '@/domain/plan'
import type { AdjustableField, AdjustmentIntent, AdjustmentReasonCode } from '@/domain/personalization'
import { toSafeOriginUrl } from '@/domain/source'
import type { ActionMode, DraftItem } from '@/domain/types'
import CoachMotion from '@/features/experience/CoachMotion.vue'
import { QUICK_EXPERIENCE_PLAN_NAME } from '@/features/quick-experience/fixture'
import { useDraftStore } from '@/stores/draft'
import { useGymtiStore } from '@/stores/gymti'
import { useLibraryStore } from '@/stores/library'
import { useLocalMediaStore } from '@/stores/local-media'
import { useTrainingStore } from '@/stores/training'

type OperationError = {
  action: 'save_as' | 'start_training'
  message: string
}

const router = useRouter()
const draft = useDraftStore()
const gymti = useGymtiStore()
const library = useLibraryStore()
const localMedia = useLocalMediaStore()
const training = useTrainingStore()

const manualName = ref('')
const manualMode = ref<ActionMode>('reps')
const showManual = ref(false)
const starting = ref(false)
const showSaveAs = ref(false)
const saveAsName = ref('')
const savingPlan = ref(false)
const saveMessage = ref('')
const operationError = ref<OperationError | null>(null)
const planNameError = ref('')
const quickPlanPending = ref(false)
const quickPlanError = ref('')
const selectedItemId = ref<string | null>(null)
const previewingItemId = ref<string | null>(null)
const previewUrl = ref<string | null>(null)
const previewError = ref('')
const confirmingItem = ref(false)
const actionSheet = ref<HTMLElement | null>(null)
const adjustmentDialog = ref<HTMLElement | null>(null)
const adjustmentOpen = ref(false)
const adjustmentIntent = ref<AdjustmentIntent | null>(null)
const adjustmentSafetyStop = ref(false)
const adjustmentError = ref('')
const adjustmentNotice = ref('')
const {
  activate: activateActionSheet,
  deactivate: deactivateActionSheet,
  onKeydown: onActionSheetKeydown,
} = useDialogFocus(actionSheet)
const {
  activate: activateAdjustmentDialog,
  deactivate: deactivateAdjustmentDialog,
  onKeydown: onAdjustmentDialogKeydown,
} = useDialogFocus(adjustmentDialog)
let previewGeneration = 0

const confirmedItems = computed(() =>
  draft.items.filter((item) => item.confirmationStatus !== 'pending'),
)
const pendingCount = computed(() => draft.items.length - confirmedItems.value.length)
const totalSets = computed(() =>
  confirmedItems.value.reduce((sum, item) => sum + (item.sets.value ?? 0), 0),
)
const estimatedMinutes = computed(() => estimatePlanMinutes(confirmedItems.value))
const personalizationContextRevision = computed(() => Math.max(
  Date.parse(gymti.current?.updatedAt ?? new Date(0).toISOString()),
  Date.parse(library.profile.updatedAt),
  Date.parse(library.preferences.updatedAt),
))
const adjustmentContextStale = computed(() => Boolean(
  draft.adjustmentProposal
  && draft.adjustmentProposal.contextRevision !== personalizationContextRevision.value,
))

const isQuickExperience = computed(() =>
  draft.plan.linkedPlanId === null && draft.plan.name === QUICK_EXPERIENCE_PLAN_NAME,
)

const selectedItem = computed(() =>
  draft.items.find((item) => item.id === selectedItemId.value) ?? null,
)

const globalValidationIssues = computed(() =>
  training.validationIssues.filter((issue) => issue.itemId === null),
)

const validationIssuesForItem = (itemId: string) =>
  training.validationIssues.filter((issue) => issue.itemId === itemId)

const hasItemIssue = (itemId: string, field: string): boolean =>
  training.validationIssues.some((issue) => issue.itemId === itemId && issue.field === field)

const validationId = (itemId: string): string => `validation-${itemId}`

const isLocalSource = (item: DraftItem): boolean => Boolean(
  item.sourceRef && (item.sourceRef.kind === 'local' || item.sourceRef.sourceId.startsWith('local:')),
)

const sourceLabel = (item: DraftItem): string =>
  item.sourceRef
    ? `${isLocalSource(item) ? '本地视频' : '来源视频'} · ${item.sourceRef.title ?? item.sourceRef.sourceId}`
    : '自建动作 · 无参考视频'

const originalUrl = (item: DraftItem): string | undefined =>
  toSafeOriginUrl(item.sourceRef?.originUrl)

const provenance = (source: string | null): string => {
  if (source === 'video') return '来自视频'
  if (source === 'rule') return '规则补全'
  if (source === 'personalized') return 'TrainPal 建议'
  if (source === 'user') return '你的调整'
  return '未填写'
}

const actionTarget = (item: DraftItem): string => {
  if (item.mode === 'duration') return `${item.durationSeconds.value ?? '—'} 秒`
  return `${item.reps.value ?? '—'} 次`
}

const fieldCopy: Record<AdjustableField, { label: string; unit: string }> = {
  sets: { label: '组数', unit: '组' },
  reps: { label: '每组次数', unit: '次' },
  durationSeconds: { label: '每组时长', unit: '秒' },
  restSeconds: { label: '休息时间', unit: '秒' },
}

const reasonCopy: Record<AdjustmentReasonCode, string> = {
  reduce_to_finish: '降低这次开始和完成的压力',
  increase_after_too_easy: '参考了这项动作最近“太轻”的反馈',
  increase_for_challenge: '按你的选择，小幅增加训练量',
  shorten_time_budget: '优先缩短这次训练的预计用时',
  extend_recovery: '增加恢复时间，不通过压缩休息提高密度',
}

const changeValue = (field: AdjustableField, value: number): string => (
  `${value} ${fieldCopy[field].unit}`
)

const openAdjustment = async (event: Event): Promise<void> => {
  adjustmentIntent.value = draft.adjustmentProposal?.intent ?? null
  adjustmentError.value = ''
  adjustmentNotice.value = ''
  adjustmentSafetyStop.value = false
  adjustmentOpen.value = true
  await activateAdjustmentDialog(event.currentTarget as HTMLElement)
}

const closeAdjustment = async (): Promise<void> => {
  adjustmentOpen.value = false
  adjustmentError.value = ''
  await deactivateAdjustmentDialog()
}

const selectAdjustmentIntent = (intent: AdjustmentIntent): void => {
  if (intent !== adjustmentIntent.value) draft.discardAdjustmentProposal()
  adjustmentIntent.value = intent
  adjustmentError.value = ''
  adjustmentNotice.value = ''
}

const generateAdjustment = (): void => {
  if (!adjustmentIntent.value) {
    adjustmentError.value = '先选择这次想调整的方向。'
    return
  }
  adjustmentError.value = ''
  adjustmentNotice.value = ''
  const proposal = draft.proposeAdjustment({
    contextRevision: personalizationContextRevision.value,
    intent: adjustmentIntent.value,
    trainingExperience: null,
    signals: [],
    hasSafetyStopSignal: adjustmentSafetyStop.value,
    generatedAt: new Date().toISOString(),
  })
  if (proposal.status === 'no_change') {
    adjustmentNotice.value = adjustmentSafetyStop.value
      ? '有不适时不会生成调整；请停止训练，并在需要时寻求专业帮助。'
      : '这份方案暂时没有合适的调整，你可以照常训练或自己修改。'
  }
}

const toggleAdjustmentSafetyStop = (): void => {
  adjustmentSafetyStop.value = !adjustmentSafetyStop.value
  if (adjustmentSafetyStop.value) {
    draft.discardAdjustmentProposal()
    adjustmentNotice.value = '有不适时不会生成调整；请停止训练，并在需要时寻求专业帮助。'
  } else {
    adjustmentNotice.value = ''
  }
}

const applyAdjustment = async (): Promise<void> => {
  const proposal = draft.adjustmentProposal
  if (adjustmentSafetyStop.value) {
    draft.discardAdjustmentProposal()
    adjustmentError.value = '有不适时不能应用调整；请停止训练。'
    return
  }
  if (!proposal) {
    adjustmentError.value = '方案已修改，请重新查看调整建议。'
    return
  }
  const result = await draft.applyAdjustmentProposal(proposal.id)
  if (result === 'conflict') {
    adjustmentError.value = '方案已修改，请重新查看调整建议。'
    return
  }
  if (result === 'persist_failed') {
    adjustmentError.value = '这次调整未保存成功，你的手动修改已保留。请重试。'
    return
  }
  adjustmentNotice.value = '已应用到当前方案。'
  await closeAdjustment()
}

const restoreBasePlan = async (): Promise<void> => {
  const result = await draft.restoreBasePlan()
  if (result.status === 'busy') {
    adjustmentNotice.value = '正在保存，请稍后再试。'
    return
  }
  if (result.status === 'persist_failed') {
    adjustmentNotice.value = '恢复未保存成功，你的手动修改已保留。请重试。'
    return
  }
  adjustmentNotice.value = result.status === 'restored'
    ? '已恢复调整前的安排，你之后的手动修改已保留。'
    : '没有可恢复的 TrainPal 调整；你的手动修改保持不变。'
}

const handleAdjustmentKeydown = (event: KeyboardEvent): void => {
  onAdjustmentDialogKeydown(event, () => { void closeAdjustment() })
}

const openEditor = async (itemId: string, trigger?: Event): Promise<void> => {
  selectedItemId.value = itemId
  await activateActionSheet(trigger?.currentTarget as HTMLElement | null | undefined)
}

const closeEditor = async (): Promise<void> => {
  selectedItemId.value = null
  previewGeneration += 1
  previewingItemId.value = null
  previewUrl.value = null
  previewError.value = ''
  await deactivateActionSheet()
}

const handleActionSheetKeydown = (event: KeyboardEvent): void => {
  onActionSheetKeydown(event, () => { void closeEditor() })
}

const openEditorWithPreview = async (item: DraftItem, event: Event): Promise<void> => {
  await openEditor(item.id, event)
  await openLocalPreview(item)
}

const removeSelected = (): void => {
  if (!selectedItem.value) return
  draft.remove(selectedItem.value.id)
  void closeEditor()
}

const confirmSelected = async (): Promise<void> => {
  if (!selectedItem.value || confirmingItem.value) return
  confirmingItem.value = true
  draft.confirmItem(selectedItem.value.id)
  try {
    await draft.flushPersist()
    await closeEditor()
  } catch {
    // Keep the sheet open and let the existing save state expose a retry.
  } finally {
    confirmingItem.value = false
  }
}

const openLocalPreview = async (item: DraftItem): Promise<void> => {
  if (!item.sourceRef || !isLocalSource(item)) return
  const sourceId = item.sourceRef.sourceId
  const currentGeneration = ++previewGeneration
  previewingItemId.value = item.id
  previewUrl.value = null
  previewError.value = ''
  try {
    const resolved = await localMedia.resolve(sourceId)
    if (previewGeneration !== currentGeneration || previewingItemId.value !== item.id) return
    previewUrl.value = resolved
    if (!resolved) previewError.value = '本地视频已不可用，请重新选择原文件'
  } catch {
    if (previewGeneration === currentGeneration && previewingItemId.value === item.id) {
      previewError.value = '本地视频读取失败，请重新选择原文件'
    }
  }
}

const reselectLocalMedia = async (item: DraftItem, event: Event): Promise<void> => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !item.sourceRef) return
  const sourceId = item.sourceRef.sourceId
  const currentGeneration = ++previewGeneration
  previewingItemId.value = item.id
  previewUrl.value = null
  previewError.value = ''
  if (!SUPPORTED_LOCAL_MEDIA_TYPES.includes(file.type as typeof SUPPORTED_LOCAL_MEDIA_TYPES[number])) {
    previewError.value = '请选择 MP4、MOV 或 WebM 视频'
    return
  }
  try {
    const duration = await probeVideoDuration(file)
    if (previewGeneration !== currentGeneration || previewingItemId.value !== item.id) return
    const expected = item.sourceRef.localMedia
    if (!expected || !fingerprintMatches(expected, file, duration)) {
      if (!window.confirm('文件信息与原视频不一致，确认仍使用这个文件吗？')) {
        previewError.value = '没有替换原视频，方案保持不变'
        return
      }
    }
    await localMedia.importFile({ file, durationSeconds: duration, sourceId })
    if (previewGeneration !== currentGeneration || previewingItemId.value !== item.id) return
    previewUrl.value = localMedia.urlFor(sourceId)
  } catch {
    if (previewGeneration === currentGeneration && previewingItemId.value === item.id) {
      previewError.value = '无法读取这个视频，请重新选择'
    }
  }
}

const addManual = (): void => {
  if (!manualName.value.trim()) return
  draft.addManualAction({ name: manualName.value, mode: manualMode.value })
  manualName.value = ''
  showManual.value = false
}

const startTraining = async (): Promise<void> => {
  if (!confirmedItems.value.length || starting.value) return
  starting.value = true
  operationError.value = null
  try {
    await draft.flushPersist()
    const executablePlan = JSON.parse(JSON.stringify({
      ...draft.plan,
      items: confirmedItems.value,
    })) as typeof draft.plan
    const result = await training.createFromDraft(
      executablePlan,
      library.preferences.coachStyleId,
    )
    if (result.ok || (!result.ok && result.code === 'active_session_exists')) {
      await router.push('/training')
    } else if (!result.ok) {
      if (result.code === 'invalid_plan') {
        await nextTick()
        const invalidCard = document.querySelector<HTMLElement>('.plan-card.has-validation-error')
        invalidCard?.focus()
        invalidCard?.scrollIntoView?.({ block: 'center' })
      } else {
        operationError.value = { action: 'start_training', message: '这次没有开始训练，请重试' }
      }
    }
  } catch {
    operationError.value = { action: 'start_training', message: '这次没有开始训练，请重试' }
  } finally {
    starting.value = false
  }
}

const updatePlanName = (event: Event): void => {
  const input = event.target as HTMLInputElement
  if (!input.value.trim()) {
    input.value = draft.plan.name
    planNameError.value = '方案名称不能为空'
    return
  }
  draft.updatePlanName(input.value)
  input.value = draft.plan.name
  planNameError.value = ''
}

const useQuickPlan = async (): Promise<void> => {
  if (quickPlanPending.value) return
  quickPlanPending.value = true
  quickPlanError.value = ''
  try {
    await draft.quiescePersistence()
    draft.adoptPersistedPlan(await library.useQuickExperience())
  } catch {
    quickPlanError.value = '快速体验方案没有载入成功，请重试'
  } finally {
    draft.resumePersistence()
    quickPlanPending.value = false
  }
}

const beginSaveAs = (): void => {
  saveAsName.value = draft.plan.name === '未命名方案' ? '' : `${draft.plan.name}（副本）`
  showSaveAs.value = true
  saveMessage.value = ''
  operationError.value = null
}

const saveAs = async (): Promise<void> => {
  if (!saveAsName.value.trim() || savingPlan.value) return
  savingPlan.value = true
  operationError.value = null
  try {
    await draft.flushPersist()
    draft.adoptPersistedPlan(await library.saveCurrentDraftAs(saveAsName.value))
    showSaveAs.value = false
    saveMessage.value = '已另存为新方案'
  } catch {
    operationError.value = { action: 'save_as', message: '另存为没有成功，请重试' }
  } finally {
    savingPlan.value = false
  }
}

const retryOperation = async (): Promise<void> => {
  if (operationError.value?.action === 'save_as') await saveAs()
  if (operationError.value?.action === 'start_training') await startTraining()
}
</script>

<template>
  <main class="plan-page tp-page tp-page--immersive">
    <header class="plan-header">
      <RouterLink to="/" class="back-link" aria-label="返回首页">←</RouterLink>
      <span>训练方案</span>
      <span class="header-spacer" aria-hidden="true" />
    </header>

    <section class="plan-hero">
      <p class="tp-kicker">{{ draft.plan.linkedPlanId ? 'SAVED PLAN' : 'CURRENT PLAN' }}</p>
      <h1 class="plan-title-heading" aria-label="当前训练方案">
        <input
          class="plan-title-input"
          :value="draft.plan.name"
          aria-label="方案名称"
          :aria-invalid="planNameError ? true : undefined"
          @change="updatePlanName"
        />
      </h1>
      <p v-if="planNameError" class="plan-name-error" role="alert">{{ planNameError }}</p>
      <div class="plan-metrics" aria-label="方案摘要">
        <span><b>{{ draft.items.length }}</b> 个动作</span>
        <span><b>{{ totalSets }}</b> 组</span>
        <span><b>{{ estimatedMinutes || '—' }}</b> 分钟约用时</span>
      </div>
    </section>

    <p v-if="isQuickExperience" class="quick-notice">快速体验方案 · 这个方案不是 AI 分析结果</p>
    <p v-if="pendingCount" class="pending-notice" role="status">
      {{ pendingCount }} 个动作需要确认，确认前不会进入训练。点击动作即可查看并决定。
    </p>
    <p v-if="saveMessage" class="save-message" role="status">{{ saveMessage }}</p>

    <section v-if="draft.items.length" class="coach-card tp-card" aria-labelledby="coach-card-title">
      <CoachMotion
        state="idle"
        :style-id="library.preferences.coachStyleId"
        :visible="library.preferences.petVisible"
      />
      <div v-if="!library.preferences.coachStyleId" class="coach-mark" aria-hidden="true">TP</div>
      <div>
        <p class="tp-kicker">TRAINPAL COACH</p>
        <h2 id="coach-card-title">
          {{ draft.appliedAdjustment ? '已为本次训练调整' : '需要更贴近你现在的状态？' }}
        </h2>
        <p v-if="draft.appliedAdjustment">调整只改变了组数、次数或时长、休息；你的手动修改仍然优先。</p>
        <p v-else>想轻松一点，还是增加挑战？先看看建议，满意再调整。</p>
      </div>
      <div class="coach-actions">
        <button type="button" data-adjustment-trigger :disabled="draft.adjustmentPending" @click="openAdjustment">
          {{ draft.appliedAdjustment ? '重新调整' : '让 TrainPal 调整这次训练' }}
        </button>
        <button
          v-if="draft.appliedAdjustment"
          type="button"
          data-restore-base-plan
          :disabled="draft.adjustmentPending"
          @click="restoreBasePlan"
        >
          恢复基础方案
        </button>
        <RouterLink to="/personalize">GYMTI 与教练风格</RouterLink>
      </div>
    </section>

    <p v-if="adjustmentNotice" class="adjustment-notice" role="status">{{ adjustmentNotice }}</p>

    <section v-if="draft.items.length" class="plan-list" aria-label="动作安排">
      <article
        v-for="(item, index) in draft.items"
        :key="item.id"
        class="plan-card"
        :class="{
          'has-validation-error': validationIssuesForItem(item.id).length,
          'needs-confirmation': item.confirmationStatus === 'pending',
        }"
        :aria-describedby="validationIssuesForItem(item.id).length ? validationId(item.id) : undefined"
        tabindex="-1"
      >
        <span class="action-index">{{ String(index + 1).padStart(2, '0') }}</span>
        <button type="button" class="action-summary" @click="openEditor(item.id, $event)">
          <span>
            <strong>
              {{ item.name }}
              <em v-if="item.confirmationStatus === 'pending'" class="pending-badge">待确认</em>
            </strong>
            <small>{{ item.sets.value ?? '—' }} 组 · 每组 {{ actionTarget(item) }} · 休息 {{ item.restSeconds.value ?? '—' }} 秒</small>
          </span>
          <b aria-hidden="true">›</b>
        </button>
        <div class="order-controls" aria-label="调整动作顺序">
          <button type="button" aria-label="上移" :disabled="index === 0" @click="draft.move(item.id, -1)">↑</button>
          <button type="button" aria-label="下移" :disabled="index === draft.items.length - 1" @click="draft.move(item.id, 1)">↓</button>
        </div>

        <div v-if="item.sourceRef" class="source-shortcut">
          <span>{{ sourceLabel(item) }}</span>
          <a
            v-if="originalUrl(item)"
            class="original-video-link"
            :href="originalUrl(item)"
            target="_blank"
            rel="noopener noreferrer"
          >查看原视频</a>
          <button
            v-else-if="isLocalSource(item)"
            type="button"
            class="local-preview-button"
            @click="openEditorWithPreview(item, $event)"
          >
            预览来源视频
          </button>
        </div>

        <div
          v-if="validationIssuesForItem(item.id).length"
          :id="validationId(item.id)"
          class="card-validation"
          role="alert"
        >
          <strong>请检查这个动作</strong>
          <p v-for="issue in validationIssuesForItem(item.id)" :key="`${issue.itemId}-${issue.field}`">
            {{ issue.message }}
          </p>
        </div>
      </article>
    </section>

    <section v-else class="empty-plan tp-card">
      <span>00</span>
      <h2>还没有训练动作</h2>
      <p>可以重新选择视频，也可以直接创建一个没有参考视频的动作。</p>
      <button type="button" :disabled="quickPlanPending" @click="useQuickPlan">
        {{ quickPlanPending ? '正在载入…' : '使用快速体验方案' }}
      </button>
      <small>这是产品示例，不是 AI 分析结果。</small>
      <p v-if="quickPlanError" class="empty-plan-error" role="alert">{{ quickPlanError }}</p>
    </section>

    <section class="plan-tools" aria-label="方案次级操作">
      <button v-if="!showManual" type="button" class="manual-trigger" @click="showManual = true">
        <span aria-hidden="true">＋</span>
        <span><strong>创建动作</strong><small>没有参考视频也可以</small></span>
      </button>
      <form v-else class="manual-form tp-card" @submit.prevent="addManual">
        <div>
          <p class="tp-kicker">MANUAL ACTION</p>
          <h2>创建自建动作</h2>
        </div>
        <label>
          动作名称
          <input v-model="manualName" autofocus placeholder="例如：平板支撑" />
        </label>
        <div class="mode-toggle">
          <button type="button" :class="{ active: manualMode === 'reps' }" @click="manualMode = 'reps'">按次数</button>
          <button type="button" :class="{ active: manualMode === 'duration' }" @click="manualMode = 'duration'">按时长</button>
        </div>
        <div class="manual-actions">
          <button type="button" @click="showManual = false">取消</button>
          <button type="submit" class="confirm" :disabled="!manualName.trim()">加入方案</button>
        </div>
      </form>

      <section v-if="draft.items.length" class="save-as-panel">
        <button v-if="!showSaveAs" type="button" @click="beginSaveAs">另存为</button>
        <form v-else @submit.prevent="saveAs">
          <label>
            新方案名称
            <input v-model="saveAsName" aria-label="新方案名称" autofocus maxlength="40" />
          </label>
          <div>
            <button type="button" @click="showSaveAs = false">取消</button>
            <button type="submit" class="confirm" :disabled="!saveAsName.trim() || savingPlan">
              {{ savingPlan ? '保存中…' : '保存副本' }}
            </button>
          </div>
        </form>
      </section>
    </section>

    <section v-if="operationError" class="operation-error" role="alert">
      <span>{{ operationError.message }}</span>
      <button
        type="button"
        :aria-label="operationError.action === 'save_as' ? '重试另存为' : '重试开始训练'"
        :disabled="operationError.action === 'save_as' ? savingPlan : starting"
        @click="retryOperation"
      >
        重试
      </button>
    </section>

    <span
      class="save-state"
      :class="{ failed: draft.persistState === 'failed' }"
      :role="draft.persistState === 'failed' ? 'alert' : 'status'"
      aria-live="polite"
    >
      <i />
      <button v-if="draft.persistState === 'failed'" type="button" aria-label="重试保存当前方案" @click="draft.retryPersist">
        {{ draft.persistMessage }}
      </button>
      <template v-else>
        {{ draft.persistMessage }}
        <small v-if="draft.plan.linkedPlanId">· 修改会同步到当前已存方案</small>
      </template>
    </span>

    <section v-if="globalValidationIssues.length" class="plan-error" role="alert">
      <strong>方案还不能开始训练</strong>
      <p v-for="issue in globalValidationIssues" :key="`${issue.itemId}-${issue.field}`">{{ issue.message }}</p>
    </section>

    <section v-if="draft.items.length || training.hasCurrent" class="start-training-panel">
      <div>
        <strong>{{ training.hasCurrent ? '已有未完成训练' : `${confirmedItems.length} 个可训练动作 · 约 ${estimatedMinutes || '—'} 分钟` }}</strong>
        <small class="training-safety-tip">
          {{ pendingCount ? `${pendingCount} 个待确认动作暂不执行 · ` : '' }}如有不适请停止，并按自身情况调整
        </small>
      </div>
      <button v-if="!training.hasCurrent" type="button" :disabled="starting || !confirmedItems.length" @click="startTraining">
        {{ starting ? '正在准备…' : '开始训练' }}
      </button>
      <RouterLink v-else to="/training">继续训练</RouterLink>
    </section>

    <template v-if="selectedItem">
      <button class="sheet-backdrop" type="button" aria-label="关闭动作编辑" @click="closeEditor" />
      <section
        ref="actionSheet"
        class="action-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="action-sheet-title"
        tabindex="-1"
        @keydown="handleActionSheetKeydown"
      >
        <header>
          <div>
            <p class="tp-kicker">ACTION {{ String(draft.items.findIndex((item) => item.id === selectedItem?.id) + 1).padStart(2, '0') }}</p>
            <h2 id="action-sheet-title">调整动作</h2>
          </div>
          <button type="button" aria-label="关闭动作编辑" @click="closeEditor">×</button>
        </header>

        <p v-if="selectedItem.confirmationStatus === 'pending'" class="confirmation-note">
          TrainPal 对这个动作的名称、片段或训练方式还不够确定。请检查后明确确认；在此之前它不会进入训练。
        </p>
        <p class="user-priority-note">你的调整优先于视频、规则和 TrainPal 建议，并会自动保存在本机。</p>

        <label class="field-stack">
          <span>动作名称</span>
          <input
            class="plan-name"
            :value="selectedItem.name"
            aria-label="动作名称"
            :aria-invalid="hasItemIssue(selectedItem.id, 'name') || undefined"
            data-dialog-initial-focus
            @change="draft.updateName(selectedItem.id, ($event.target as HTMLInputElement).value)"
          />
        </label>

        <div class="mode-toggle" aria-label="训练方式">
          <button type="button" :class="{ active: selectedItem.mode === 'reps' }" @click="draft.updateMode(selectedItem.id, 'reps')">按次数</button>
          <button type="button" :class="{ active: selectedItem.mode === 'duration' }" @click="draft.updateMode(selectedItem.id, 'duration')">按时长</button>
        </div>

        <div class="parameter-grid">
          <label>
            <span>组数 <em>{{ provenance(selectedItem.sets.source) }}</em></span>
            <input type="number" min="1" :value="selectedItem.sets.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'sets') || undefined" @input="draft.updateValue(selectedItem.id, 'sets', Number(($event.target as HTMLInputElement).value) || null)" />
          </label>
          <label v-if="selectedItem.mode === 'reps'">
            <span>每组次数 <em>{{ provenance(selectedItem.reps.source) }}</em></span>
            <input type="number" min="1" :value="selectedItem.reps.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'reps') || undefined" @input="draft.updateValue(selectedItem.id, 'reps', Number(($event.target as HTMLInputElement).value) || null)" />
          </label>
          <label v-else>
            <span>每组秒数 <em>{{ provenance(selectedItem.durationSeconds.source) }}</em></span>
            <input type="number" min="1" :value="selectedItem.durationSeconds.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'durationSeconds') || undefined" @input="draft.updateValue(selectedItem.id, 'durationSeconds', Number(($event.target as HTMLInputElement).value) || null)" />
          </label>
          <label>
            <span>休息秒数 <em>{{ provenance(selectedItem.restSeconds.source) }}</em></span>
            <input type="number" min="0" :value="selectedItem.restSeconds.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'restSeconds') || undefined" @input="draft.updateValue(selectedItem.id, 'restSeconds', Number(($event.target as HTMLInputElement).value) || 0)" />
          </label>
          <label>
            <span>重量 kg <em>{{ provenance(selectedItem.weightKg.source) }}</em></span>
            <input type="number" min="0" step="0.5" placeholder="留空" :value="selectedItem.weightKg.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'weightKg') || undefined" @input="draft.updateValue(selectedItem.id, 'weightKg', Number(($event.target as HTMLInputElement).value) || null)" />
          </label>
        </div>

        <section class="source-detail">
          <div>
            <p class="tp-kicker">SOURCE</p>
            <strong>{{ sourceLabel(selectedItem) }}</strong>
          </div>
          <div v-if="selectedItem.segment.value" class="segment-line">
            <span>参考时间段</span>
            <b>{{ selectedItem.segment.value.start_seconds.toFixed(1) }}—{{ selectedItem.segment.value.end_seconds.toFixed(1) }} 秒</b>
            <em>{{ provenance(selectedItem.segment.source) }}</em>
          </div>
          <a v-if="originalUrl(selectedItem)" class="original-video-link" :href="originalUrl(selectedItem)" target="_blank" rel="noopener noreferrer">查看原视频</a>
          <button v-else-if="isLocalSource(selectedItem)" type="button" class="local-preview-button" @click="openLocalPreview(selectedItem)">
            {{ previewingItemId === selectedItem.id ? '重新读取来源视频' : '预览来源视频' }}
          </button>
          <div v-if="previewingItemId === selectedItem.id" class="local-preview">
            <video v-if="previewUrl" :src="previewUrl" controls playsinline preload="metadata" />
            <p v-else role="status">{{ previewError || '正在读取本地视频…' }}</p>
            <label v-if="!previewUrl" class="reselect-local-media">
              重新选择原视频
              <input type="file" :accept="SUPPORTED_LOCAL_MEDIA_TYPES.join(',')" @change="reselectLocalMedia(selectedItem, $event)" />
            </label>
            <p v-if="localMedia.current?.sourceId === selectedItem.sourceRef?.sourceId && localMedia.storageMessage" class="local-storage-message" role="status">{{ localMedia.storageMessage }}</p>
          </div>
          <p class="source-help">来源片段仅作参考播放；训练次数或计时以你当前设置为准。</p>
        </section>

        <footer class="sheet-actions">
          <button type="button" @click="draft.duplicate(selectedItem.id)">复制动作</button>
          <button type="button" class="danger" @click="removeSelected">删除动作</button>
          <button
            v-if="selectedItem.confirmationStatus === 'pending'"
            type="button"
            class="done"
            :disabled="confirmingItem"
            @click="confirmSelected"
          >
            {{ confirmingItem ? '正在确认…' : '确认并加入' }}
          </button>
          <button v-else type="button" class="done" @click="closeEditor">完成</button>
        </footer>
      </section>
    </template>

    <template v-if="adjustmentOpen">
      <button class="sheet-backdrop" type="button" aria-label="关闭训练调整" @click="closeAdjustment" />
      <section
        ref="adjustmentDialog"
        class="action-sheet adjustment-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="adjustment-dialog-title"
        tabindex="-1"
        @keydown="handleAdjustmentKeydown"
      >
        <header>
          <div>
            <p class="tp-kicker">TRAINPAL</p>
            <h2 id="adjustment-dialog-title">调整这次训练</h2>
          </div>
          <button type="button" aria-label="关闭训练调整" @click="closeAdjustment">×</button>
        </header>

        <p class="user-priority-note">视频里的明确要求、你的手动修改和重量都保持不变。</p>

        <button
          type="button"
          class="adjustment-safety-stop"
          data-adjustment-safety-stop
          :aria-pressed="adjustmentSafetyStop"
          @click="toggleAdjustmentSafetyStop"
        >
          <span aria-hidden="true">{{ adjustmentSafetyStop ? '✓' : '○' }}</span>
          我现在有疼痛、眩晕或其他需要停止训练的不适
        </button>

        <div class="adjustment-intents" aria-label="选择调整方向">
          <button
            type="button"
            data-dialog-initial-focus
            data-adjustment-intent="easier_to_finish"
            :class="{ active: adjustmentIntent === 'easier_to_finish' }"
            :aria-pressed="adjustmentIntent === 'easier_to_finish'"
            @click="selectAdjustmentIntent('easier_to_finish')"
          >
            <strong>更容易完成</strong><span>适度降低次数或时长，必要时增加休息</span>
          </button>
          <button
            type="button"
            data-adjustment-intent="more_challenging"
            :class="{ active: adjustmentIntent === 'more_challenging' }"
            :aria-pressed="adjustmentIntent === 'more_challenging'"
            @click="selectAdjustmentIntent('more_challenging')"
          >
            <strong>更有挑战</strong><span>小幅增加次数或时长，不压缩休息</span>
          </button>
          <button
            type="button"
            data-adjustment-intent="shorter_session"
            :class="{ active: adjustmentIntent === 'shorter_session' }"
            :aria-pressed="adjustmentIntent === 'shorter_session'"
            @click="selectAdjustmentIntent('shorter_session')"
          >
            <strong>时间更短</strong><span>小幅减少训练量，不缩短休息</span>
          </button>
        </div>

        <button type="button" class="generate-adjustment" data-generate-adjustment :disabled="draft.adjustmentPending" @click="generateAdjustment">
          看看调整建议
        </button>

        <p v-if="adjustmentError" class="inline-error" role="alert">{{ adjustmentError }}</p>
        <p v-if="adjustmentNotice" class="adjustment-dialog-notice" role="status">{{ adjustmentNotice }}</p>
        <p v-if="adjustmentContextStale" class="context-stale-note" role="status">
          TrainPal 对你的了解已经更新；可以应用这份提案，也可以按当前信息重新生成。
        </p>

        <section v-if="draft.adjustmentProposal?.status === 'ready'" class="adjustment-change-list" aria-label="调整差异">
          <article v-for="change in draft.adjustmentProposal.changes" :key="`${change.itemId}-${change.field}`">
            <div><strong>{{ change.itemName }}</strong><span>{{ fieldCopy[change.field].label }}</span></div>
            <b>{{ changeValue(change.field, change.before.value) }} → {{ changeValue(change.field, change.after.value) }}</b>
            <p>{{ reasonCopy[change.reasonCode] }}</p>
          </article>
        </section>

        <footer class="sheet-actions">
          <button type="button" @click="closeAdjustment">稍后再说</button>
          <button
            v-if="draft.adjustmentProposal?.status === 'ready'"
            type="button"
            class="done"
            data-apply-adjustment
            :disabled="draft.adjustmentPending"
            @click="applyAdjustment"
          >
            应用这次调整
          </button>
        </footer>
      </section>
    </template>
  </main>
</template>

<style scoped>
.plan-page { position: relative; max-width: 860px; padding-bottom: calc(132px + var(--tp-task-reserve, 0px) + env(safe-area-inset-bottom)); }
.plan-header { display: grid; grid-template-columns: 44px 1fr 44px; align-items: center; margin-bottom: 34px; }
.plan-header > span { color: var(--tp-muted); font-size: 13px; font-weight: 800; text-align: center; }
.back-link { display: grid; width: 44px; height: 44px; place-items: center; border: 1px solid var(--tp-line); border-radius: 50%; color: var(--tp-ink); background: var(--tp-surface); font-size: 22px; text-decoration: none; }
.header-spacer { width: 44px; }

.plan-hero { padding-bottom: 22px; border-bottom: 1px solid var(--tp-line); }
.plan-title-heading { margin: 8px 0 14px; }
.plan-title-input { width: 100%; min-height: 48px; padding: 0; border: 0; color: var(--tp-ink); background: transparent; font: 700 clamp(40px, 11vw, 68px)/.92 var(--font-display), var(--font-cn); letter-spacing: -.03em; }
.plan-title-input:focus { text-decoration: underline; text-decoration-color: rgb(217 75 43 / 24%); text-underline-offset: 7px; }
.plan-name-error { margin: -6px 0 12px; color: var(--tp-danger); font-size: 12px; }
.plan-metrics { display: flex; flex-wrap: wrap; gap: 10px 24px; }
.plan-metrics span { color: var(--tp-muted); font-size: 12px; }
.plan-metrics b { margin-right: 4px; color: var(--tp-ink); font: 700 25px/1 var(--font-display); }
.quick-notice,
.pending-notice,
.save-message { margin: 14px 0 0; padding: 11px 13px; border-left: 3px solid var(--tp-primary); border-radius: 0 10px 10px 0; color: var(--tp-muted); background: rgb(217 75 43 / 7%); font-size: 12px; line-height: 1.55; }
.pending-notice { border-left-color: #9A6A1D; color: #72501B; background: rgb(154 106 29 / 8%); }
.save-message { border-left-color: var(--tp-success); color: var(--tp-success); background: rgb(73 115 59 / 7%); }

.coach-card { display: grid; grid-template-columns: auto 1fr; gap: 12px 14px; margin-top: 20px; padding: 18px; }
.coach-mark { display: grid; width: 48px; height: 48px; place-items: center; border-radius: 18px 18px 18px 5px; color: var(--tp-ink); background: var(--tp-secondary); font: 800 18px/1 var(--font-display); transform: rotate(-2deg); }
.coach-card h2 { margin: 5px 0 4px; font-size: 20px; }
.coach-card p:not(.tp-kicker) { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.coach-actions { display: flex; grid-column: 2; flex-wrap: wrap; align-items: center; gap: 5px 12px; }
.coach-actions button,
.coach-actions a { min-height: 44px; padding: 0; border: 0; color: var(--tp-primary-readable); background: transparent; font-size: 12px; font-weight: 800; text-decoration: none; }
.coach-actions a { display: grid; place-items: center; }
.adjustment-notice { margin: 10px 0 0; color: var(--tp-success); font-size: 12px; line-height: 1.6; }

.plan-list { display: grid; gap: 10px; margin-top: 22px; }
.plan-card { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 18px; background: var(--tp-surface); box-shadow: 0 8px 28px rgb(42 51 45 / 6%); }
.plan-card.has-validation-error { border-color: var(--tp-danger); }
.plan-card.needs-confirmation { border-style: dashed; border-color: #B48A49; background: #FFF9ED; }
.plan-card.has-validation-error:focus { outline: 2px solid var(--tp-danger); outline-offset: 3px; }
.action-index { color: var(--tp-primary); font: 700 21px/1 var(--font-display); text-align: center; }
.action-summary { display: flex; min-width: 0; min-height: 76px; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 8px; border: 0; color: var(--tp-ink); background: transparent; text-align: left; }
.action-summary span { min-width: 0; }
.action-summary strong,
.action-summary small { display: block; }
.action-summary strong { overflow: hidden; font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
.pending-badge { display: inline-flex; margin-left: 5px; padding: 4px 7px; border-radius: 999px; color: #72501B; background: #F1DEBA; font-size: 11px; font-style: normal; vertical-align: 2px; }
.action-summary small { margin-top: 6px; color: var(--tp-muted); font-size: 12px; }
.action-summary > b { color: var(--tp-primary); font-size: 28px; font-weight: 400; }
.order-controls { display: grid; gap: 2px; padding-right: 8px; }
.order-controls button { width: 44px; min-height: 44px; border: 0; color: var(--tp-muted); background: transparent; }
.order-controls button:disabled { opacity: .22; }
.source-shortcut { display: flex; grid-column: 2 / -1; min-width: 0; align-items: center; justify-content: space-between; gap: 8px; padding: 0 12px 10px 8px; }
.source-shortcut > span { min-width: 0; overflow: hidden; color: var(--tp-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.source-shortcut a,
.source-shortcut button { flex: 0 0 auto; min-height: 44px; padding: 0 4px; border: 0; color: var(--tp-primary-readable); background: transparent; font-size: 11px; font-weight: 800; text-decoration: none; }
.source-shortcut a { display: grid; place-items: center; }
.card-validation { grid-column: 1 / -1; padding: 10px 14px; border-top: 1px solid rgb(179 38 30 / 20%); color: var(--tp-danger); background: rgb(179 38 30 / 5%); }
.card-validation strong { font-size: 12px; }
.card-validation p { margin: 3px 0 0; font-size: 12px; }

.empty-plan { margin-top: 22px; padding: 38px 22px; text-align: center; }
.empty-plan > span { color: var(--tp-primary); font: 700 26px/1 var(--font-display); }
.empty-plan h2 { margin: 8px 0; }
.empty-plan p { max-width: 330px; margin: auto; color: var(--tp-muted); font-size: 13px; line-height: 1.7; }
.empty-plan button { min-height: 48px; margin-top: 18px; padding: 0 20px; border: 0; border-radius: 999px; color: var(--tp-surface); background: var(--tp-primary-readable); font-weight: 800; }
.empty-plan small { display: block; margin-top: 9px; color: var(--tp-muted); font-size: 11px; }
.empty-plan .empty-plan-error { margin-top: 10px; color: var(--tp-danger); }

.plan-tools { display: grid; gap: 12px; margin-top: 14px; }
.manual-trigger { display: flex; width: 100%; min-height: 68px; align-items: center; gap: 12px; padding: 12px 15px; border: 1px dashed #BDB9AC; border-radius: 16px; color: var(--tp-ink); background: transparent; text-align: left; }
.manual-trigger > span:first-child { display: grid; width: 38px; height: 38px; place-items: center; border-radius: 50%; color: var(--tp-surface); background: var(--tp-primary); font-size: 21px; }
.manual-trigger strong,
.manual-trigger small { display: block; }
.manual-trigger small { margin-top: 3px; color: var(--tp-muted); }
.manual-form { display: grid; gap: 14px; padding: 18px; }
.manual-form h2 { margin: 5px 0 0; }
.manual-form label,
.save-as-panel label,
.field-stack { display: grid; gap: 6px; color: var(--tp-muted); font-size: 12px; }
.manual-form input,
.save-as-panel input,
.field-stack input { min-height: 48px; padding: 11px 13px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: var(--tp-surface); }
.mode-toggle { display: inline-flex; justify-self: start; gap: 4px; padding: 4px; border: 1px solid var(--tp-line); border-radius: 13px; background: #F7F3EA; }
.mode-toggle button { min-height: 44px; padding: 0 14px; border: 0; border-radius: 9px; color: var(--tp-muted); background: transparent; }
.mode-toggle button.active { color: var(--tp-surface); background: var(--tp-ink); font-weight: 800; }
.manual-actions,
.save-as-panel form > div { display: flex; justify-content: flex-end; gap: 8px; }
.manual-actions button,
.save-as-panel button { min-height: 44px; padding: 0 15px; border: 1px solid var(--tp-line); border-radius: 999px; color: var(--tp-muted); background: var(--tp-surface); }
.manual-actions .confirm,
.save-as-panel .confirm { color: var(--tp-surface); border-color: var(--tp-primary); background: var(--tp-primary-readable); font-weight: 800; }
.save-as-panel { display: grid; gap: 10px; padding: 8px 0; }
.save-as-panel > button { justify-self: start; }
.save-as-panel form { display: grid; gap: 10px; padding: 16px; border: 1px solid var(--tp-line); border-radius: 16px; background: var(--tp-surface); }

.save-state { display: flex; min-height: 44px; align-items: center; gap: 7px; margin-top: 8px; color: var(--tp-muted); font-size: 11px; }
.save-state i { width: 7px; height: 7px; border-radius: 50%; background: var(--tp-success); }
.save-state small { color: inherit; }
.save-state.failed { color: var(--tp-danger); }
.save-state.failed i { background: var(--tp-danger); }
.save-state button { min-height: 44px; padding: 0; border: 0; color: inherit; background: transparent; text-decoration: underline; }
.operation-error,
.plan-error { margin-top: 12px; padding: 13px 15px; border: 1px solid rgb(179 38 30 / 28%); border-radius: 14px; color: var(--tp-danger); background: rgb(179 38 30 / 5%); font-size: 12px; }
.operation-error { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.operation-error button { min-height: 44px; padding: 0 14px; border: 1px solid currentcolor; border-radius: 999px; color: inherit; background: transparent; font-weight: 800; }
.plan-error p { margin: 4px 0 0; }

.start-training-panel { position: fixed; right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); left: max(14px, env(safe-area-inset-left)); z-index: 20; display: flex; max-width: 820px; align-items: center; justify-content: space-between; gap: 12px; margin: auto; padding: 12px 12px 12px 16px; border: 1px solid rgb(28 40 34 / 16%); border-radius: 22px; color: var(--tp-training-ink); background: var(--tp-training-surface); box-shadow: var(--tp-shadow-float); }
.start-training-panel strong,
.start-training-panel small { display: block; }
.start-training-panel strong { font-size: 13px; }
.start-training-panel small { margin-top: 4px; color: #B9C0BB; font-size: 11px; }
.start-training-panel button,
.start-training-panel a { display: grid; min-height: 52px; flex: 0 0 auto; place-items: center; padding: 0 22px; border: 0; border-radius: 999px; color: var(--tp-surface); background: var(--tp-primary-readable); font-weight: 800; text-decoration: none; }
.start-training-panel button:disabled { opacity: .55; }

.sheet-backdrop { position: fixed; inset: 0; z-index: 60; width: 100%; border: 0; background: rgb(14 19 17 / 48%); backdrop-filter: blur(3px); }
.action-sheet { position: fixed; right: 0; bottom: 0; left: 0; z-index: 61; display: grid; max-height: min(88dvh, 820px); gap: 16px; overflow-y: auto; padding: 20px clamp(16px, 4vw, 26px) calc(20px + env(safe-area-inset-bottom)); border-radius: 28px 28px 0 0; color: var(--tp-ink); background: var(--tp-surface); box-shadow: 0 -24px 70px rgb(14 19 17 / 24%); }
.action-sheet > header { display: flex; align-items: center; justify-content: space-between; }
.action-sheet h2 { margin: 5px 0 0; font-size: 26px; }
.action-sheet > header button { width: 44px; min-height: 44px; border: 1px solid var(--tp-line); border-radius: 50%; color: var(--tp-ink); background: transparent; font-size: 25px; }
.user-priority-note { margin: 0; padding: 11px 13px; border-left: 3px solid var(--tp-secondary); color: var(--tp-muted); background: rgb(165 186 99 / 12%); font-size: 12px; line-height: 1.6; }
.confirmation-note { margin: 0; padding: 12px 13px; border: 1px solid #DDBD85; border-radius: 13px; color: #72501B; background: #FFF7E8; font-size: 12px; line-height: 1.6; }
.plan-name { font-size: 17px; font-weight: 800; }
.parameter-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.parameter-grid label { display: grid; gap: 6px; }
.parameter-grid label > span { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px; color: var(--tp-muted); font-size: 11px; }
.parameter-grid em,
.segment-line em { color: var(--tp-primary-readable); font-size: 11px; font-style: normal; }
.parameter-grid input { width: 100%; min-height: 48px; padding: 10px 12px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: #F7F3EA; font: 700 19px/1 var(--font-display); }
.source-detail { display: grid; gap: 10px; padding: 15px; border: 1px solid var(--tp-line); border-radius: 16px; background: #F7F3EA; }
.source-detail strong { display: block; margin-top: 5px; font-size: 13px; }
.segment-line { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; color: var(--tp-muted); font-size: 11px; }
.segment-line b { margin-left: auto; color: var(--tp-ink); font: 700 15px/1 var(--font-display); }
.source-detail > a,
.source-detail > button { justify-self: start; min-height: 44px; padding: 0; border: 0; color: var(--tp-primary-readable); background: transparent; font-size: 12px; font-weight: 800; text-decoration: none; }
.source-detail > a { display: grid; place-items: center; }
.source-help { margin: 0; color: var(--tp-muted); font-size: 11px; line-height: 1.6; }
.local-preview { display: grid; gap: 8px; }
.local-preview video { width: 100%; max-height: 280px; border-radius: 12px; object-fit: contain; background: #000; }
.local-preview p { margin: 0; color: var(--tp-muted); font-size: 11px; }
.local-preview .local-storage-message { color: var(--tp-success); }
.reselect-local-media { position: relative; display: grid; min-height: 44px; place-items: center; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 999px; color: var(--tp-ink); font-size: 12px; font-weight: 800; }
.reselect-local-media:focus-within { outline: 2px solid var(--tp-focus); outline-offset: 3px; }
.reselect-local-media input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.sheet-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.sheet-actions button { min-height: 46px; padding: 0 16px; border: 1px solid var(--tp-line); border-radius: 999px; color: var(--tp-muted); background: transparent; }
.sheet-actions .danger { color: var(--tp-danger); }
.sheet-actions .done { margin-left: auto; color: var(--tp-surface); border-color: var(--tp-ink); background: var(--tp-ink); font-weight: 800; }
.adjustment-intents { display: grid; gap: 8px; }
.adjustment-safety-stop { display: flex; min-height: 48px; align-items: center; gap: 9px; padding: 10px 12px; border: 1px solid #DDBD85; border-radius: 13px; color: #72501B; background: #FFF7E8; text-align: left; }
.adjustment-safety-stop[aria-pressed="true"] { color: var(--tp-surface); border-color: var(--tp-danger); background: var(--tp-danger); }
.adjustment-intents button { display: grid; min-height: 68px; gap: 4px; padding: 13px 15px; border: 1px solid var(--tp-line); border-radius: 15px; color: var(--tp-ink); background: #F7F3EA; text-align: left; }
.adjustment-intents button.active { color: var(--tp-surface); border-color: var(--tp-ink); background: var(--tp-ink); }
.adjustment-intents strong,
.adjustment-intents span { display: block; }
.adjustment-intents span { color: var(--tp-muted); font-size: 11px; line-height: 1.45; }
.adjustment-intents button.active span { color: #D4D9D3; }
.generate-adjustment { min-height: 48px; border: 1px solid var(--tp-primary); border-radius: 999px; color: var(--tp-surface); background: var(--tp-primary-readable); font-weight: 800; }
.adjustment-change-list { display: grid; gap: 8px; }
.adjustment-change-list article { display: grid; gap: 7px; padding: 13px; border: 1px solid var(--tp-line); border-radius: 14px; background: #F7F3EA; }
.adjustment-change-list article > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.adjustment-change-list article span,
.adjustment-change-list article p { color: var(--tp-muted); font-size: 11px; }
.adjustment-change-list article b { color: var(--tp-primary-readable); font: 700 18px/1 var(--font-display), var(--font-cn); }
.adjustment-change-list article p,
.adjustment-dialog-notice { margin: 0; line-height: 1.55; }
.adjustment-dialog-notice { color: var(--tp-success); font-size: 12px; }
.context-stale-note { margin: 0; padding: 10px 12px; border-left: 3px solid #9A6A1D; color: #72501B; background: #FFF7E8; font-size: 11px; line-height: 1.55; }
.inline-error { margin: 0; color: var(--tp-danger); font-size: 12px; }

@media (min-width: 760px) {
  .coach-card { grid-template-columns: auto 1fr auto; align-items: center; }
  .coach-actions { grid-column: 3; grid-row: 1; justify-content: end; }
  .action-sheet { top: 0; right: 0; bottom: 0; left: auto; width: min(520px, 100%); max-height: none; border-radius: 28px 0 0 28px; }
}

@media (max-width: 359px) {
  .parameter-grid { grid-template-columns: 1fr; }
  .start-training-panel { align-items: stretch; }
  .start-training-panel > div { display: none; }
  .start-training-panel button,
  .start-training-panel a { width: 100%; }
}
</style>
