<script setup lang="ts">
import SourceTips from '@/features/experience/SourceTips.vue'
import NumericStepper from '@/components/NumericStepper.vue'
import { matchingFeedback } from '@/personalization/feedback'
import { createAdjustmentReview } from '@/personalization/adjustment-review'
import type { AdjustmentReview, TrainingExperience } from '@/domain/personalization'
import TrainingReminderSettings from '@/features/experience/TrainingReminderSettings.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

import { useDialogFocus } from '@/composables/useDialogFocus'
import { fingerprintMatches, probeVideoDuration, SUPPORTED_LOCAL_MEDIA_TYPES } from '@/domain/local-media'
import { estimatePlanMinutes } from '@/domain/plan'
import type { AdjustableField, AdjustmentIntent, AdjustmentReasonCode } from '@/domain/personalization'
import { toSafeOriginUrl } from '@/domain/source'
import type { ActionMode, DraftItem } from '@/domain/types'
import FloatingCoach from '@/features/experience/FloatingCoach.vue'
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
const reminderPreferencePending = computed(() => library.preferencePending)
const showSaveAs = ref(false)
const saveAsName = ref('')
const savingPlan = ref(false)
onBeforeRouteLeave(() => !savingPlan.value)
const saveMessage = ref('')
const operationError = ref<OperationError | null>(null)
const planNameError = ref('')
const quickPlanPending = ref(false)
const quickPlanError = ref('')
const selectedItemId = ref<string | null>(null)
const expandedItemId = ref<string | null>(null)
const editorMode = ref<'edit' | 'source'>('edit')
const toggleAction = (id: string): void => { expandedItemId.value = expandedItemId.value === id ? null : id }
let inputVisibilityFrame: number | null = null
const keepInputVisible = (): void => {
  if (inputVisibilityFrame !== null) cancelAnimationFrame(inputVisibilityFrame)
  inputVisibilityFrame = requestAnimationFrame(() => {
    inputVisibilityFrame = null
    const input = document.activeElement
    if (!(input instanceof HTMLInputElement) || !input.closest('.inline-action-editor')) return
    const viewport = window.visualViewport
    const viewportBottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight
    const controls = document.querySelector<HTMLElement>('.start-training-panel')?.getBoundingClientRect()
    const availableBottom = Math.min(viewportBottom, controls?.top ?? viewportBottom) - 16
    const rect = input.getBoundingClientRect()
    if (rect.bottom > availableBottom) window.scrollBy({ top: rect.bottom - availableBottom, behavior: 'instant' })
  })
}
onMounted(() => {
  window.addEventListener('resize', keepInputVisible)
  window.visualViewport?.addEventListener('resize', keepInputVisible)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', keepInputVisible)
  window.visualViewport?.removeEventListener('resize', keepInputVisible)
  if (inputVisibilityFrame !== null) cancelAnimationFrame(inputVisibilityFrame)
})
const previewingItemId = ref<string | null>(null)
const previewUrl = ref<string | null>(null)
const previewError = ref('')
const confirmingItem = ref(false)
const actionSheet = ref<HTMLElement | null>(null)
const adjustmentDialog = ref<HTMLElement | null>(null)
const adjustmentOpen = ref(false)
const adjustmentStep = ref<'intent' | 'preview'>('intent')
const adjustmentIntent = ref<AdjustmentIntent | null>(null)
const adjustmentSafetyStop = ref(false)
const adjustmentError = ref('')
const adjustmentNotice = ref('')
const availableMinutes = ref<number | null>(null)
const energy = ref<'usual' | 'tired'>('usual')
const experience = ref<TrainingExperience | null>(library.profile.trainingExperience ?? null)
const review = ref<AdjustmentReview | null>(null)
const generatingAdjustment = ref(false)
let adjustmentGeneration = 0
const expandedReviewId = ref<string | null>(null)
const experienceLabels = { beginner: '刚开始训练', intermediate: '有一定经验', advanced: '长期规律训练' }
const feedbackEvidence = computed(() => {
  if (adjustmentStep.value !== 'preview') return []
  const signals = draft.adjustmentProposal?.context?.signals ?? []
  const current = matchingFeedback(draft.plan, library.actionFeedback)
  return [...new Set(signals.map(signal => signal.itemId))].map(itemId => {
    const latest = signals.filter(signal => signal.itemId === itemId).sort((a, b) => b.recordedAt.localeCompare(a.recordedAt))[0]!
    return { ...latest, name: draft.items.find(item => item.id === itemId)?.name ?? '动作',
      label: { too_hard: '上次太累', just_right: '上次合适', too_easy: '上次太轻' }[latest.value] }
  }).filter(signal => current.some(entry => entry.itemId === signal.itemId
    && entry.recordedAt === signal.recordedAt && entry.value === signal.value))
})
const contextSummary = computed(() => {
  const context = draft.adjustmentProposal?.context
  if (!context) return '请按当前情况重新生成建议'
  return [context.trainingExperience ? experienceLabels[context.trainingExperience] : '未填训练经验',
    context.availableMinutes ? `可用 ${context.availableMinutes} 分钟` : '不限时间',
    context.energy === 'tired' ? '今天有点累' : '精力正常',
    context.signals.length
      ? feedbackEvidence.value.length === new Set(context.signals.map(signal => signal.itemId)).size
        ? `参考 ${feedbackEvidence.value.length} 个动作的历史反馈`
        : '原反馈已更新或清除，请重新生成建议'
      : '无匹配的历史反馈，按本次输入调整'].join(' · ')
})
const reviewValue = (itemId: string, field: AdjustableField) => review.value?.values.find(value => value.itemId === itemId && value.field === field)
const reviewedMinutes = computed(() => {
  const items = JSON.parse(JSON.stringify(draft.items)) as DraftItem[]
  for (const value of review.value?.values ?? []) {
    const item = items.find(item => item.id === value.itemId)
    if (item) item[value.field].value = value.value
  }
  return estimatePlanMinutes(items.filter(item => item.confirmationStatus !== 'pending'))
})
const updateReview = (itemId: string, field: AdjustableField, value: number | null): void => {
  const entry = reviewValue(itemId, field)
  if (entry) { entry.value = value; entry.edited = true }
  adjustmentError.value = ''
}
const invalidNumber = (field: AdjustableField, value: number | null): boolean =>
  value === null || !Number.isSafeInteger(value) || value < (field === 'restSeconds' ? 0 : 1)
const reviewIssues = (itemId: string) => (review.value?.values ?? [])
  .filter(value => value.itemId === itemId && invalidNumber(value.field, value.value))
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
const pendingItems = computed(() => draft.items.filter((item) => item.confirmationStatus === 'pending'))
const pendingCount = computed(() => pendingItems.value.length)
const totalSets = computed(() =>
  confirmedItems.value.reduce((sum, item) => sum + (item.sets.value ?? 0), 0),
)
const estimatedMinutes = computed(() => estimatePlanMinutes(confirmedItems.value))
const personalizationContextRevision = computed(() => Math.max(
  Date.parse(gymti.current?.updatedAt ?? new Date(0).toISOString()),
  Date.parse(library.profile.updatedAt),
  Date.parse(library.preferences.updatedAt),
  library.feedbackRevision,
))
const adjustmentContextStale = computed(() => Boolean(
  draft.adjustmentProposal
  && draft.adjustmentProposal.contextRevision !== personalizationContextRevision.value,
))
const adjustmentGroups = computed(() => {
  const changes = draft.adjustmentProposal?.changes ?? []
  return [...new Set(changes.map((change) => change.itemId))].map((itemId) => ({
    itemId,
    name: changes.find((change) => change.itemId === itemId)!.itemName,
    changes: changes.filter((change) => change.itemId === itemId),
  }))
})
const formatEstimate = (seconds: number) => `${Math.round(seconds / 6) / 10} 分钟`
const editorOpen = computed(() => Boolean(selectedItemId.value || adjustmentOpen.value))
let previousBodyOverflow = ''
watch(editorOpen, (open) => {
  if (open) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  } else document.body.style.overflow = previousBodyOverflow
})
onBeforeUnmount(() => { if (editorOpen.value) document.body.style.overflow = previousBodyOverflow })

const isQuickExperience = computed(() =>
  draft.items.some((item) => item.origin === 'quick_experience')
  || draft.plan.name === QUICK_EXPERIENCE_PLAN_NAME,
)

const selectedItem = computed(() =>
  draft.items.find((item) => item.id === selectedItemId.value) ?? null,
)
const reviewedTipIndexes = computed(() => (selectedItem.value?.sourceTips ?? []).flatMap((tip, index) =>
  selectedItem.value?.reviewedSourceTips?.some(review => review.review_status === 'approved'
    && review.review_action_id === selectedItem.value?.id
    && review.review_action_revision === (selectedItem.value?.actionRevision ?? 0)
    && review.source_id === selectedItem.value?.sourceRef?.sourceId
    && JSON.stringify(review.tip) === JSON.stringify(tip)) ? [index] : []))

const globalValidationIssues = computed(() =>
  training.validationIssues.filter((issue) => issue.itemId === null),
)
watch(() => new Map(draft.items.map(item => [item.id, {
  sets: item.sets.value, reps: item.reps.value, durationSeconds: item.durationSeconds.value,
  restSeconds: item.restSeconds.value,
}])), (current, previous) => {
  training.validationIssues = training.validationIssues.filter(issue => {
    if (!issue.itemId || !['sets', 'reps', 'durationSeconds', 'restSeconds'].includes(issue.field)) return true
    const field = issue.field as AdjustableField
    return current.get(issue.itemId)?.[field] === previous.get(issue.itemId)?.[field]
  })
})

const validationIssuesForItem = (itemId: string) => {
  const item = draft.items.find(item => item.id === itemId)
  const fields: AdjustableField[] = ['sets', item?.mode === 'duration' ? 'durationSeconds' : 'reps', 'restSeconds']
  const current = item ? fields.filter(field => invalidNumber(field, item[field].value)).map(field => ({
    itemId, field, message: `${fieldCopy[field].label}需为${field === 'restSeconds' ? '不小于 0 的整数' : '正整数'}`,
  })) : []
  return [...current, ...training.validationIssues.filter(issue => issue.itemId === itemId
    && !current.some(currentIssue => currentIssue.field === issue.field))]
}

const hasItemIssue = (itemId: string, field: string): boolean =>
  validationIssuesForItem(itemId).some(issue => issue.field === field)

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
  increase_for_challenge: '小幅增加本次训练量',
  shorten_time_budget: '优先缩短这次训练的预计用时',
  extend_recovery: '增加恢复时间，不通过压缩休息提高密度',
}
const adjustmentReason = (itemId: string, reason: AdjustmentReasonCode): string =>
  reason === 'increase_after_too_easy' && !feedbackEvidence.value.some(signal => signal.itemId === itemId)
    ? '原反馈已更新或清除，请重新生成建议'
    : reasonCopy[reason]

const changeValue = (field: AdjustableField, value: number): string => (
  `${value} ${fieldCopy[field].unit}`
)

const openAdjustment = async (event: Event): Promise<void> => {
  adjustmentGeneration += 1
  experience.value = library.profile.trainingExperience ?? null
  review.value = null
  expandedItemId.value = null
  adjustmentIntent.value = draft.adjustmentProposal?.intent ?? null
  adjustmentError.value = ''
  adjustmentNotice.value = ''
  adjustmentSafetyStop.value = false
  adjustmentStep.value = 'intent'
  adjustmentOpen.value = true
  await activateAdjustmentDialog(event.currentTarget as HTMLElement)
}

const closeAdjustment = async (): Promise<void> => {
  if (draft.adjustmentPending) return
  adjustmentGeneration += 1
  review.value = null
  adjustmentOpen.value = false
  adjustmentError.value = ''
  await deactivateAdjustmentDialog()
}

const selectAdjustmentIntent = (intent: AdjustmentIntent): void => {
  if (generatingAdjustment.value) return
  if (intent !== adjustmentIntent.value) draft.discardAdjustmentProposal()
  adjustmentIntent.value = intent
  adjustmentError.value = ''
  adjustmentNotice.value = ''
}


const generateAdjustment = async (): Promise<void> => {
  if (draft.adjustmentPending || generatingAdjustment.value) return
  if (!adjustmentIntent.value) {
    adjustmentError.value = '先选择这次想调整的方向。'
    return
  }
  adjustmentError.value = ''
  adjustmentNotice.value = ''
  if (availableMinutes.value !== null && (!Number.isFinite(availableMinutes.value) || availableMinutes.value < 1)) {
    adjustmentError.value = '可用时间请填大于 0 的分钟数，或留空。'
    return
  }
  if (experience.value !== (library.profile.trainingExperience ?? null)) {
    const generation = adjustmentGeneration
    generatingAdjustment.value = true
    try { await library.saveProfile({ ...library.profile, trainingExperience: experience.value }) }
    catch { adjustmentError.value = '训练经验未保存成功，请重试。'; return }
    finally { generatingAdjustment.value = false }
    if (generation !== adjustmentGeneration || !adjustmentOpen.value) return
  }
  const proposal = draft.proposeAdjustment({
    contextRevision: personalizationContextRevision.value,
    intent: adjustmentIntent.value,
    trainingExperience: library.profile.trainingExperience ?? null,
    signals: matchingFeedback(draft.plan, library.actionFeedback),
    availableMinutes: availableMinutes.value,
    energy: energy.value,
    hasSafetyStopSignal: adjustmentSafetyStop.value,
    generatedAt: new Date().toISOString(),
  })
  review.value = createAdjustmentReview(proposal)
  expandedReviewId.value = proposal.changes[0]?.itemId ?? null
  if (availableMinutes.value !== null && proposal.impact.estimatedSecondsAfter > availableMinutes.value * 60) {
    adjustmentNotice.value = `按当前保护范围，预计仍需 ${formatEstimate(proposal.impact.estimatedSecondsAfter)}，未达到 ${availableMinutes.value} 分钟目标。`
  }
  if (proposal.status === 'no_change') {
    adjustmentNotice.value = adjustmentSafetyStop.value
      ? '有不适时不会生成调整；请停止训练，并在需要时寻求专业帮助。'
      : availableMinutes.value !== null && adjustmentIntent.value === 'shorter_session' && proposal.impact.estimatedSecondsBefore <= availableMinutes.value * 60
        ? '当前预计用时已在可用时间内，保留原方案。'
        : `当前方案没有可安全自动调整的字段，预计 ${formatEstimate(proposal.impact.estimatedSecondsAfter)}，可以手动修改。`
  }
  adjustmentStep.value = 'preview'
  void nextTick(() => {
    adjustmentDialog.value?.scrollTo?.({ top: 0 })
    adjustmentDialog.value?.querySelector<HTMLElement>('[data-preview-heading]')?.focus()
  })
}

const editAdjustmentIntent = async (): Promise<void> => {
  adjustmentStep.value = 'intent'
  adjustmentError.value = ''
  await nextTick()
  adjustmentDialog.value?.querySelector<HTMLElement>('[data-dialog-initial-focus]')?.focus()
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
  if (!review.value) { adjustmentError.value = '请重新查看调整建议。'; return }
  const invalid = review.value.values.find(value => invalidNumber(value.field, value.value))
  if (invalid) {
    adjustmentError.value = '请先修正标出的数字。'
    expandedReviewId.value = invalid.itemId
    await nextTick()
    document.getElementById(`review-${invalid.itemId}`)?.querySelector<HTMLInputElement>('[aria-invalid="true"]')?.focus()
    return
  }
  const result = await draft.applyAdjustmentProposal(proposal.id, review.value)
  if (result === 'invalid') { adjustmentError.value = '请检查数字：组数、次数和时长需为正整数，休息可为 0。'; return }
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

const openEditor = async (itemId: string, trigger?: Event, mode: 'edit' | 'source' = 'edit'): Promise<void> => {
  expandedItemId.value = null
  editorMode.value = mode
  selectedItemId.value = itemId
  await activateActionSheet(trigger?.currentTarget as HTMLElement | null | undefined)
}

const closeEditor = async (): Promise<void> => {
  const summary = selectedItem.value
    ? document.getElementById(`action-summary-${selectedItem.value.id}`)
    : null
  selectedItemId.value = null
  previewGeneration += 1
  previewingItemId.value = null
  previewUrl.value = null
  previewError.value = ''
  await deactivateActionSheet()
  if (document.activeElement === document.body && summary?.isConnected) summary.focus()
}

const handleActionSheetKeydown = (event: KeyboardEvent): void => {
  onActionSheetKeydown(event, () => { void closeEditor() })
}

const removeSelected = (): void => {
  if (!selectedItem.value) return
  draft.remove(selectedItem.value.id)
  void closeEditor()
}

const confirmSelected = async (): Promise<void> => {
  if (!selectedItem.value || confirmingItem.value) return
  const confirmedItemId = selectedItem.value.id
  confirmingItem.value = true
  draft.confirmItem(confirmedItemId)
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
  if (!confirmedItems.value.length || starting.value || reminderPreferencePending.value) return
  starting.value = true
  operationError.value = null
  try {
    await draft.flushPersistForTraining()
    const result = await training.createFromDraft(
      draft.plan,
      library.preferences.coachStyleId,
      library.preferences.speechReminders ?? { automatic_speech: false, confirmed: false },
    )
    if (result.ok || (!result.ok && result.code === 'active_session_exists')) {
      await router.push('/training')
    } else if (!result.ok) {
      if (result.code === 'invalid_plan') {
        expandedItemId.value = training.validationIssues.find(issue => issue.itemId)?.itemId ?? null
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
    await draft.flushPersist()
    await draft.quiescePersistence()
    draft.adoptPersistedPlan(await library.useQuickExperience())
  } catch {
    quickPlanError.value = '预设方案没有载入成功，请重试'
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
    await draft.quiescePersistence()
    draft.adoptPersistedPlan(await library.saveCurrentDraftAs(saveAsName.value))
    showSaveAs.value = false
    saveMessage.value = '已创建方案副本'
  } catch {
    operationError.value = { action: 'save_as', message: '复制方案没有成功，请重试' }
  } finally {
    draft.resumePersistence()
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
    <header class="plan-header" :inert="savingPlan || undefined">
      <RouterLink to="/train" class="back-link" aria-label="返回训练">←</RouterLink>
      <span>训练方案</span>
      <span class="header-spacer" aria-hidden="true" />
    </header>

    <p v-if="savingPlan" role="status">正在创建方案副本…</p>
    <div class="plan-workspace" :inert="editorOpen || savingPlan || undefined">
      <aside class="plan-overview" aria-label="本次训练概览">
        <section class="plan-hero" data-coach-avoid>
          <p class="tp-kicker">{{ draft.plan.linkedPlanId ? '已保存的方案' : '本次训练' }}</p>
          <h1 class="plan-title-heading" aria-label="当前训练方案">
            <input
              class="plan-title-input"
              data-coach-avoid
              :value="draft.plan.name"
              aria-label="方案名称"
              :aria-invalid="planNameError ? true : undefined"
              @change="updatePlanName"
            />
          </h1>
          <p v-if="planNameError" class="plan-name-error" role="alert">{{ planNameError }}</p>
          <div class="plan-metrics" aria-label="方案摘要">
            <span><b>{{ confirmedItems.length }}</b> 个可训练动作</span>
            <span><b>{{ totalSets }}</b> 组</span>
            <span><b>{{ estimatedMinutes || '—' }}</b> 分钟 · 预计</span>
          </div>
        </section>

        <p v-if="isQuickExperience" class="quick-notice" data-coach-avoid>含快速体验动作 · 这些示例不是 AI 分析结果</p>
        <section v-if="pendingCount" class="plan-readiness" aria-label="开练前核对" data-coach-avoid>
          <template v-if="pendingCount">
            <h2>{{ pendingCount }} 个动作待确认，暂不进入训练</h2>
            <p>先核对名称、片段和训练量，也可以直接练已确认的动作。</p>
            <ul class="pending-review-list">
              <li v-for="pendingItem in pendingItems" :key="pendingItem.id">
                <button type="button" @click="openEditor(pendingItem.id, $event)">
                  <span>{{ pendingItem.name }}</span><span>去核对 ↗</span>
                </button>
              </li>
            </ul>
          </template>
          <details class="workout-guide">
            <summary>开练后怎么跟练</summary>
            <p>次数练完后点“完成本组”；计时动作到时自动结束。同动作休息后自动倒数，换动作时等你准备好。</p>
          </details>
        </section>
        <p v-if="saveMessage" class="save-message" role="status">{{ saveMessage }}</p>

        <section v-if="draft.items.length" class="coach-card" aria-labelledby="coach-card-title">
          <div class="coach-copy" data-coach-avoid>
            <h2 id="coach-card-title">
              {{ draft.appliedAdjustment ? '已为本次训练调整' : 'AI 个性化调整' }}
            </h2>
          </div>
          <div class="coach-actions" data-coach-avoid>
            <button type="button" data-adjustment-trigger :disabled="draft.adjustmentPending" @click="openAdjustment">
              {{ draft.appliedAdjustment ? '再次调整' : '按我的情况调整' }} <span aria-hidden="true">›</span>
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
          </div>
        </section>

        <p v-if="adjustmentNotice" class="adjustment-notice" role="status">{{ adjustmentNotice }}</p>
      </aside>

      <div class="plan-body">
        <div v-if="draft.items.length" class="list-heading" data-coach-avoid><h2>动作安排</h2><span>按顺序训练 · 点击调整</span></div>

        <section v-if="draft.items.length" class="plan-list" aria-label="动作安排">
          <article
            v-for="(item, index) in draft.items"
            :key="item.id"
            class="plan-card"
            data-coach-avoid
            :class="{
              'has-validation-error': validationIssuesForItem(item.id).length,
              'needs-confirmation': item.confirmationStatus === 'pending',
              'is-expanded': expandedItemId === item.id,
            }"
            :aria-describedby="validationIssuesForItem(item.id).length ? validationId(item.id) : undefined"
            tabindex="-1"
          >
            <span class="action-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <button :id="`action-summary-${item.id}`" type="button" class="action-summary" :aria-expanded="expandedItemId === item.id" :aria-controls="`action-editor-${item.id}`" data-coach-avoid @click="toggleAction(item.id)">
              <span>
                <strong>
                  {{ item.name }}
                  <em v-if="item.confirmationStatus === 'pending'" class="pending-badge">待确认</em>
                </strong>
                <small>{{ item.sets.value ?? '—' }} 组 · 每组 {{ actionTarget(item) }} · 休息 {{ item.restSeconds.value ?? '—' }} 秒</small>
              </span>
              <b aria-hidden="true">{{ expandedItemId === item.id ? '⌃' : '⌄' }}</b>
            </button>

            <div v-if="expandedItemId === item.id" :id="`action-editor-${item.id}`" class="inline-action-editor" :aria-label="`${item.name}训练量`" @focusin="keepInputVisible">
              <div class="inline-number-grid">
                <NumericStepper label="组数" :model-value="item.sets.value" :invalid="hasItemIssue(item.id, 'sets')" :disabled="starting || draft.adjustmentPending" @update:model-value="draft.updateValue(item.id, 'sets', $event)" />
                <NumericStepper v-if="item.mode === 'reps'" label="每组次数" :model-value="item.reps.value" :invalid="hasItemIssue(item.id, 'reps')" :disabled="starting || draft.adjustmentPending" @update:model-value="draft.updateValue(item.id, 'reps', $event)" />
                <NumericStepper v-else label="每组秒数" :model-value="item.durationSeconds.value" :invalid="hasItemIssue(item.id, 'durationSeconds')" :disabled="starting || draft.adjustmentPending" @update:model-value="draft.updateValue(item.id, 'durationSeconds', $event)" />
                <NumericStepper label="休息秒数" :min="0" :model-value="item.restSeconds.value" :invalid="hasItemIssue(item.id, 'restSeconds')" :disabled="starting || draft.adjustmentPending" @update:model-value="draft.updateValue(item.id, 'restSeconds', $event)" />
              </div>
              <div class="inline-editor-links">
                <button v-if="item.sourceRef" type="button" @click="openEditor(item.id, $event, 'source')">查看视频来源 ›</button>
                <button type="button" data-full-editor @click="openEditor(item.id, $event)">{{ item.confirmationStatus === 'pending' ? '核对并确认动作' : '更多设置' }} ›</button>
              </div>
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
            {{ quickPlanPending ? '正在载入…' : '查看预设方案' }}
          </button>
          <small>这是产品示例，不是 AI 分析结果。</small>
          <p v-if="quickPlanError" class="empty-plan-error" role="alert">{{ quickPlanError }}</p>
        </section>

        <section class="plan-tools" aria-label="方案次级操作" data-coach-avoid>
          <button v-if="!showManual" type="button" class="manual-trigger" @click="showManual = true">
            <span aria-hidden="true">＋</span>
            <span><strong>创建动作</strong><small>没有参考视频也可以</small></span>
          </button>
          <form v-else class="manual-form tp-card" @submit.prevent="addManual">
            <div>
              <p class="tp-kicker">手动添加</p>
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
            <button v-if="!showSaveAs" type="button" @click="beginSaveAs">复制方案</button>
            <form v-else @submit.prevent="saveAs">
              <label>
                新方案名称
                <input v-model="saveAsName" aria-label="新方案名称" autofocus maxlength="40" />
              </label>
              <div>
                <button type="button" @click="showSaveAs = false">取消</button>
                <button type="submit" class="confirm" :disabled="!saveAsName.trim() || savingPlan">
                  {{ savingPlan ? '创建中…' : '创建副本' }}
                </button>
              </div>
            </form>
          </section>
        </section>

        <details v-if="draft.items.length" class="extra-settings"><summary>陪练与提醒</summary><TrainingReminderSettings :suspended="editorOpen || savingPlan || starting" /><RouterLink to="/personalize">GYMTI 与教练风格</RouterLink></details>

        <section v-if="operationError" class="operation-error" role="alert">
          <span>{{ operationError.message }}</span>
          <button
            type="button"
            :aria-label="operationError.action === 'save_as' ? '重试复制方案' : '重试开始训练'"
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
            <small v-if="draft.plan.linkedPlanId">· 修改会更新方案库中的这份方案</small>
          </template>
        </span>

        <section v-if="globalValidationIssues.length" class="plan-error" role="alert">
          <strong>方案还不能开始训练</strong>
          <p v-for="issue in globalValidationIssues" :key="`${issue.itemId}-${issue.field}`">{{ issue.message }}</p>
        </section>
      </div>
    </div>

    <section v-if="draft.items.length || training.hasCurrent" class="start-training-panel" data-coach-avoid :inert="editorOpen || savingPlan || undefined">
      <div>
        <strong>{{ training.hasCurrent ? '已有未完成训练' : `${confirmedItems.length} 个可训练动作 · 约 ${estimatedMinutes || '—'} 分钟` }}</strong>
        <small class="training-safety-tip">
          {{ pendingCount ? `${pendingCount} 个待确认动作暂不执行 · ` : '' }}如有不适请停止，并按自身情况调整
        </small>
      </div>
      <button v-if="!training.hasCurrent" type="button" :disabled="starting || reminderPreferencePending || !confirmedItems.length" @click="startTraining">
        {{ starting ? '正在准备…' : '开始训练' }}
      </button>
      <RouterLink v-else to="/training">继续训练</RouterLink>
    </section>

    <FloatingCoach v-if="library.preferences.coachStyleId" :suspended="editorOpen || savingPlan" @adjust="openAdjustment" />

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
            <p class="tp-kicker">动作 {{ String(draft.items.findIndex((item) => item.id === selectedItem?.id) + 1).padStart(2, '0') }}</p>
            <h2 id="action-sheet-title">{{ editorMode === 'source' ? `${selectedItem.name} · 视频来源` : '调整动作' }}</h2>
          </div>
          <button type="button" aria-label="关闭动作编辑" @click="closeEditor">×</button>
        </header>

        <template v-if="editorMode === 'edit'">
          <p v-if="selectedItem.confirmationStatus === 'pending'" class="confirmation-note">
            TrainPal 对这个动作的名称、片段或训练方式还不够确定。请检查后明确确认；在此之前它不会进入训练。
          </p>
          <p class="user-priority-note">修改后会自动保存，按你设置的数值训练。</p>

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
              <input type="number" min="1" :value="selectedItem.sets.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'sets') || undefined" @input="draft.updateValue(selectedItem.id, 'sets', (($event.target as HTMLInputElement).value.trim() === '' ? null : Number(($event.target as HTMLInputElement).value)))" />
            </label>
            <label v-if="selectedItem.mode === 'reps'">
              <span>每组次数 <em>{{ provenance(selectedItem.reps.source) }}</em></span>
              <input type="number" min="1" :value="selectedItem.reps.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'reps') || undefined" @input="draft.updateValue(selectedItem.id, 'reps', (($event.target as HTMLInputElement).value.trim() === '' ? null : Number(($event.target as HTMLInputElement).value)))" />
            </label>
            <label v-else>
              <span>每组秒数 <em>{{ provenance(selectedItem.durationSeconds.source) }}</em></span>
              <input type="number" min="1" :value="selectedItem.durationSeconds.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'durationSeconds') || undefined" @input="draft.updateValue(selectedItem.id, 'durationSeconds', (($event.target as HTMLInputElement).value.trim() === '' ? null : Number(($event.target as HTMLInputElement).value)))" />
            </label>
            <label>
              <span>休息秒数 <em>{{ provenance(selectedItem.restSeconds.source) }}</em></span>
              <input type="number" min="0" :value="selectedItem.restSeconds.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'restSeconds') || undefined" @input="draft.updateValue(selectedItem.id, 'restSeconds', (($event.target as HTMLInputElement).value.trim() === '' ? null : Number(($event.target as HTMLInputElement).value)))" />
            </label>
            <label>
              <span>重量 kg <em>{{ provenance(selectedItem.weightKg.source) }}</em></span>
              <input type="number" min="0" step="0.5" placeholder="留空" :value="selectedItem.weightKg.value ?? ''" :aria-invalid="hasItemIssue(selectedItem.id, 'weightKg') || undefined" @input="draft.updateValue(selectedItem.id, 'weightKg', (($event.target as HTMLInputElement).value.trim() === '' ? null : Number(($event.target as HTMLInputElement).value)))" />
            </label>
          </div>

          <div class="order-controls" aria-label="调整动作顺序">
            <span>动作顺序</span>
            <button type="button" aria-label="上移" :disabled="draft.items[0]?.id === selectedItem.id" @click="draft.move(selectedItem.id, -1)">↑ 上移</button>
            <button type="button" aria-label="下移" :disabled="draft.items.at(-1)?.id === selectedItem.id" @click="draft.move(selectedItem.id, 1)">↓ 下移</button>
          </div>
        </template>

        <section class="source-detail">
          <p v-if="editorMode === 'source'" class="source-parameter-note">本次训练：{{ selectedItem.sets.value ?? '—' }} 组 · 每组 {{ actionTarget(selectedItem) }} · 休息 {{ selectedItem.restSeconds.value ?? '—' }} 秒</p>
          <div v-if="selectedItem.sourceParameters" class="source-parameter-note"><strong>视频明确参数</strong><p><span v-if="selectedItem.sourceParameters.sets != null">{{ selectedItem.sourceParameters.sets }} 组 · </span><span v-if="selectedItem.sourceParameters.reps != null">{{ selectedItem.sourceParameters.reps }} 次 · </span><span v-if="selectedItem.sourceParameters.duration_seconds != null">每组 {{ selectedItem.sourceParameters.duration_seconds }} 秒 · </span><span v-if="selectedItem.sourceParameters.rest_seconds != null">休息 {{ selectedItem.sourceParameters.rest_seconds }} 秒</span></p></div>
          <div v-if="selectedItem.parameterConflicts?.length" class="source-parameter-note" role="note">
            <strong>视频中有不同说法，请核对后设置</strong>
            <p>请对照出处选择适合你的次数或时长，确认后再开始训练。</p>
            <div v-for="conflict in selectedItem.parameterConflicts" :key="conflict.field">
              <b>{{ ({ mode: '训练方式', sets: '组数', reps: '次数', duration_seconds: '时长', rest_seconds: '休息' })[conflict.field] }}</b>
              <ul>
                <li v-for="(alternative, index) in conflict.alternatives" :key="index">
                  <template v-if="conflict.field === 'reps'">{{ alternative.parameters.reps }}<template v-if="alternative.parameters.reps_max">–{{ alternative.parameters.reps_max }}</template> 次</template>
                  <template v-else-if="conflict.field === 'mode'">
                    {{ alternative.parameters.mode === 'reps' || alternative.parameters.reps != null ? '按次数' : '按时长' }}
                    <span v-if="alternative.parameters.reps != null"> · {{ alternative.parameters.reps }}<template v-if="alternative.parameters.reps_max != null">–{{ alternative.parameters.reps_max }}</template> 次</span>
                    <span v-if="alternative.parameters.duration_seconds != null"> · {{ alternative.parameters.duration_seconds }} 秒</span>
                  </template>
                  <template v-else>{{ alternative.parameters[conflict.field] }} {{ conflict.field === 'sets' ? '组' : '秒' }}</template>
                  <small v-for="(span, spanIndex) in alternative.evidence" :key="spanIndex"> · {{ span.type === 'speech' ? '语音' : '字幕' }} {{ span.start_seconds.toFixed(1) }}–{{ span.end_seconds.toFixed(1) }} 秒</small>
                </li>
              </ul>
            </div>
          </div>
          <SourceTips
            :tips="selectedItem.sourceTips ?? []"
            :review="{ enabled: selectedItem.confirmationStatus === 'confirmed' && Boolean(selectedItem.sourceRef && selectedItem.segment.value), approvedIndexes: reviewedTipIndexes }"
            @review="draft.reviewSourceTips(selectedItem.id, $event)"
          />
          <p v-if="selectedItem.sourceParameters?.reps_max" class="source-parameter-note">
            视频建议：<template v-if="selectedItem.sourceParameters.sets">{{ selectedItem.sourceParameters.sets }} 组，</template>每组 {{ selectedItem.sourceParameters.reps }}～{{ selectedItem.sourceParameters.reps_max }} 次
          </p>
          <div>
            <p class="tp-kicker">动作来源</p>
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
          <button v-if="editorMode === 'edit'" type="button" @click="draft.duplicate(selectedItem.id)">复制动作</button>
          <button v-if="editorMode === 'edit'" type="button" class="danger" @click="removeSelected">删除动作</button>
          <button
            v-if="editorMode === 'edit' && selectedItem.confirmationStatus === 'pending'"
            type="button"
            class="done"
            :disabled="confirmingItem"
            @click="confirmSelected"
          >
            {{ confirmingItem ? '正在确认…' : '确认并加入' }}
          </button>
          <button v-else type="button" class="done" @click="closeEditor">{{ editorMode === 'source' ? '返回方案' : '完成' }}</button>
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
            <p class="tp-kicker">{{ adjustmentStep === 'intent' ? '01 / 02 · 选择方向' : '02 / 02 · 核对变化' }}</p>
            <h2 id="adjustment-dialog-title">调整这次训练</h2>
          </div>
          <button type="button" aria-label="关闭训练调整" @click="closeAdjustment">×</button>
        </header>

        <template v-if="adjustmentStep === 'intent'">
          <div class="adjustment-context">
            <label>训练经验<select v-model="experience" aria-label="训练经验" :disabled="generatingAdjustment"><option :value="null">暂不填写</option><option v-for="(label, value) in experienceLabels" :key="value" :value="value">{{ label }}</option></select></label>
            <NumericStepper v-model="availableMinutes" label="本次可用分钟" :disabled="generatingAdjustment" optional />
            <label>今天的精力<select v-model="energy" aria-label="今天的精力" :disabled="generatingAdjustment"><option value="usual">和平时一样</option><option value="tired">有点累</option></select></label>
            <small>训练经验会保存到资料；时间和精力只用于本次调整。</small>
          </div>
          <p class="adjustment-intro">这次训练，你更希望怎样？</p>

          <div class="adjustment-intents" aria-label="选择调整方向">
            <button
              type="button"
              data-dialog-initial-focus
              data-adjustment-intent="easier_to_finish"
              :disabled="generatingAdjustment"
              :class="{ active: adjustmentIntent === 'easier_to_finish' }"
              :aria-pressed="adjustmentIntent === 'easier_to_finish'"
              @click="selectAdjustmentIntent('easier_to_finish')"
            >
              <strong>更容易完成</strong><span>适度降低次数或时长，必要时增加休息</span>
            </button>
            <button
              type="button"
              data-adjustment-intent="more_challenging"
              :disabled="generatingAdjustment"
              :class="{ active: adjustmentIntent === 'more_challenging' }"
              :aria-pressed="adjustmentIntent === 'more_challenging'"
              @click="selectAdjustmentIntent('more_challenging')"
            >
              <strong>更有挑战</strong><span>小幅增加次数或时长，不压缩休息</span>
            </button>
            <button
              type="button"
              data-adjustment-intent="shorter_session"
              :disabled="generatingAdjustment"
              :class="{ active: adjustmentIntent === 'shorter_session' }"
              :aria-pressed="adjustmentIntent === 'shorter_session'"
              @click="selectAdjustmentIntent('shorter_session')"
            >
              <strong>控制用时</strong><span>优先减少最长动作，总变化不超过 20%</span>
            </button>
          </div>
        </template>

        <template v-else>
          <h3 data-preview-heading tabindex="-1" class="preview-heading">{{ draft.adjustmentProposal?.status === 'ready' ? '看看这些变化是否合适' : '这次保留当前方案' }}</h3>
          <p v-if="draft.adjustmentProposal?.status === 'ready'" class="adjustment-impact">
            <span>预计用时</span>
            <b>{{ formatEstimate(draft.adjustmentProposal.impact.estimatedSecondsBefore) }} → 约 {{ reviewedMinutes }} 分钟</b>
            <small>{{ adjustmentGroups.length }} 个动作有变化 · 确认后生效</small>
          </p>
        </template>
        <details class="adjustment-basis" :open="adjustmentStep === 'intent'">
          <summary>{{ adjustmentStep === 'preview' ? '本次调整依据与保护' : '健康与保护' }}</summary><p v-if="adjustmentStep === 'preview'">{{ contextSummary }}</p><p v-for="signal in feedbackEvidence" :key="signal.itemId">{{ signal.name }} · {{ signal.label }} · {{ signal.recordedAt.slice(0, 10) }}</p><p v-if="adjustmentStep === 'preview' && draft.adjustmentProposal?.context?.energy === 'tired' && adjustmentIntent === 'more_challenging'">今天有点累，本次按更容易完成的方向调整。</p>

          <button
            type="button"
            class="adjustment-safety-stop"
            data-adjustment-safety-stop
            :disabled="generatingAdjustment"
            :aria-pressed="adjustmentSafetyStop"
            @click="toggleAdjustmentSafetyStop"
          >
            <span aria-hidden="true">{{ adjustmentSafetyStop ? '✓' : '○' }}</span>
            我现在有疼痛、眩晕或其他需要停止训练的不适
          </button>

          <p class="user-priority-note">保留视频明确要求、你的手动修改和重量。</p>
        </details>

        <p v-if="adjustmentError" class="inline-error" role="alert">{{ adjustmentError }}</p>
        <p v-if="adjustmentNotice" class="adjustment-dialog-notice" role="status">{{ adjustmentNotice }}</p>
        <p v-if="adjustmentContextStale" class="context-stale-note" role="status">
          TrainPal 对你的了解已经更新；可以应用这份提案，也可以按当前信息重新生成。
        </p>

        <section v-if="adjustmentStep === 'preview' && draft.adjustmentProposal?.status === 'ready'" class="adjustment-change-list" aria-label="调整差异">
          <article v-for="group in adjustmentGroups" :key="group.itemId">
            <button class="review-summary" type="button" :aria-expanded="expandedReviewId === group.itemId" :aria-controls="`review-${group.itemId}`" @click="expandedReviewId = expandedReviewId === group.itemId ? null : group.itemId"><strong>{{ group.name }}</strong><span>{{ group.changes.length }} 项调整 {{ expandedReviewId === group.itemId ? '⌃' : '⌄' }}</span></button>
            <p v-if="reviewIssues(group.itemId).length" class="review-number-error" role="alert">请检查{{ reviewIssues(group.itemId).map(value => fieldCopy[value.field].label).join('、') }}</p>
            <div v-if="expandedReviewId === group.itemId" :id="`review-${group.itemId}`">
              <div v-for="change in group.changes" :key="change.field" class="change-row">
                <p>{{ changeValue(change.field, change.before.value) }} → {{ changeValue(change.field, reviewValue(group.itemId, change.field)?.value ?? change.after.value) }} · {{ reviewValue(group.itemId, change.field)?.edited ? '你的调整' : 'AI 建议' }}</p>
                <NumericStepper :label="fieldCopy[change.field].label" :min="change.field === 'restSeconds' ? 0 : 1" :model-value="reviewValue(group.itemId, change.field)?.value ?? null" :invalid="invalidNumber(change.field, reviewValue(group.itemId, change.field)?.value ?? null)" :disabled="draft.adjustmentPending" @update:model-value="updateReview(group.itemId, change.field, $event)" />
                <p>{{ adjustmentReason(group.itemId, change.reasonCode) }}</p>
              </div>
            </div>
          </article>
        </section>

        <footer class="sheet-actions">
          <button v-if="adjustmentStep === 'preview'" type="button" data-edit-adjustment-intent @click="editAdjustmentIntent">修改方向</button>
          <button v-else type="button" @click="closeAdjustment">稍后再说</button>
          <button v-if="adjustmentStep === 'intent'" type="button" class="done generate-adjustment" data-generate-adjustment :disabled="draft.adjustmentPending || generatingAdjustment" @click="generateAdjustment">{{ generatingAdjustment ? '正在保存资料…' : '查看调整建议' }} <span aria-hidden="true">→</span></button>
          <button
            v-if="adjustmentStep === 'preview' && draft.adjustmentProposal?.status === 'ready'"
            type="button"
            class="done"
            data-apply-adjustment
            :disabled="draft.adjustmentPending"
            @click="applyAdjustment"
          >
            应用这次调整
          </button>
          <button v-else-if="adjustmentStep === 'preview'" type="button" class="done" @click="closeAdjustment">保留当前方案</button>
        </footer>
      </section>
    </template>
  </main>
</template>

<style scoped>
.plan-page { position: relative; width: 100%; max-width: 1160px; padding-bottom: calc(132px + var(--tp-task-reserve, 0px) + env(safe-area-inset-bottom)); }
.plan-header { display: grid; grid-template-columns: 44px 1fr 44px; align-items: center; margin-bottom: 24px; }
.plan-header > span { color: var(--tp-muted); font-size: 13px; font-weight: 500; text-align: center; }
.back-link { display: grid; width: 44px; height: 44px; place-items: center; border: 0; border-radius: 8px; color: var(--tp-ink); background: transparent; font-size: 22px; text-decoration: none; }
.header-spacer { width: 44px; }
.plan-workspace { display: grid; gap: 32px; }
.plan-overview, .plan-body { min-width: 0; }
.plan-page .tp-kicker { color: var(--tp-muted); font: 500 12px/1.4 var(--font-cn); letter-spacing: .04em; text-transform: none; }
.list-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.list-heading h2 { margin: 0; font-size: 16px; font-weight: 600; }
.list-heading span { color: var(--tp-muted); font-size: 11px; }

.plan-hero { padding-bottom: 22px; border-bottom: 1px solid var(--tp-line); }
.plan-title-heading { margin: 8px 0 14px; }
.plan-title-heading:focus { outline: none; }
.plan-title-input { width: 100%; min-height: 48px; padding: 0; border: 0; border-radius: 3px; color: var(--tp-ink); background: transparent; font: 600 clamp(24px, 5vw, 30px)/1.35 var(--font-cn); letter-spacing: -.035em; }
.plan-title-input:focus { text-decoration: underline; text-decoration-color: rgb(25 205 192 / 24%); text-underline-offset: 7px; }
.plan-name-error { margin: -6px 0 12px; color: var(--tp-danger); font-size: 12px; }
.plan-metrics { display: flex; flex-wrap: wrap; gap: 10px 20px; }
.plan-metrics span { color: var(--tp-muted); font-size: 12px; }
.plan-metrics b { margin-right: 4px; color: var(--tp-ink); font: 500 30px/1 var(--font-display); font-variant-numeric: tabular-nums; }
.quick-notice,
.pending-notice,
.save-message { margin: 14px 0 0; padding: 11px 13px; border-left: 3px solid var(--tp-primary); border-radius: 0 10px 10px 0; color: var(--tp-muted); background: rgb(25 205 192 / 7%); font-size: 12px; line-height: 1.55; }
.pending-notice { border-left-color: #9A6A1D; color: #72501B; background: rgb(154 106 29 / 8%); }
.save-message { border-left-color: var(--tp-success); color: var(--tp-success); background: rgb(73 115 59 / 7%); }

.coach-card { display: grid; gap: 8px; margin-top: 24px; padding: 20px; border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-surface-raised); }
.coach-card h2 { margin: 6px 0; font-size: 15px; font-weight: 600; line-height: 1.5; }
.coach-card p:not(.tp-kicker) { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.coach-card.has-floating-coach .coach-copy { width: calc(100% - 112px); min-height: 132px; }
.coach-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0 12px; }
.coach-actions button,
.coach-actions a { min-height: 44px; padding: 0; border: 0; color: var(--tp-primary-readable); background: transparent; font-size: 12px; font-weight: 600; text-decoration: none; }
.coach-actions a { display: grid; place-items: center; }
.coach-actions a, .coach-actions [data-restore-base-plan] { color: var(--tp-muted); font-weight: 400; }
.adjustment-notice { margin: 10px 0 0; color: var(--tp-success); font-size: 12px; line-height: 1.6; }

.source-parameter-note { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.source-parameter-note small { display: block; }
.plan-list { display: grid; gap: 8px; }
.adjustment-context { display: grid; gap: 12px; }
.adjustment-context label { display: grid; gap: 6px; font-size: 14px; }
.adjustment-context select { min-width: 0; min-height: 44px; padding: 8px; border: 1px solid var(--tp-line); border-radius: 10px; color: var(--tp-ink); background: var(--tp-surface); }
.adjustment-context small, .adjustment-basis { color: var(--tp-muted); font-size: 13px; line-height: 1.6; }
.adjustment-basis summary { min-height: 44px; cursor: pointer; }
.review-summary { display: flex; width: 100%; min-height: 52px; justify-content: space-between; align-items: center; gap: 12px; border: 0; background: transparent; color: var(--tp-ink); text-align: left; }
.review-summary span { color: var(--tp-muted); font-size: 13px; }
.review-number-error { margin: 4px 0; color: var(--tp-danger); font-size: 13px; }
.inline-action-editor { grid-column: 1 / -1; padding: 0 16px 12px; border-top: 1px solid var(--tp-line); }
.inline-number-grid { display: grid; gap: 12px; padding-top: 16px; }
.inline-editor-links { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; margin-top: 8px; }
.inline-editor-links button { min-height: 44px; padding: 0; border: 0; color: var(--tp-primary-readable); background: transparent; font-size: 14px; }
.plan-card.is-expanded { border-color: var(--tp-primary-readable); }
.extra-settings { margin-top: 12px; color: var(--tp-muted); font-size: 14px; }
.extra-settings summary { display: flex; align-items: center; min-height: 44px; cursor: pointer; }
.extra-settings > a { display: inline-flex; align-items: center; min-height: 44px; }
@media (min-width: 600px) { .inline-number-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.plan-card { display: grid; grid-template-columns: 48px minmax(0, 1fr); align-items: center; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-surface); transition: border-color 180ms, background 180ms; }
.plan-card:focus-within { border-color: var(--tp-line); }
.plan-card.has-validation-error { border-color: var(--tp-danger); }
.plan-card.needs-confirmation { border-style: dashed; border-color: #B48A49; background: #FFF9ED; }
.plan-card.has-validation-error:focus { outline: 2px solid var(--tp-danger); outline-offset: 3px; }
.action-index { color: var(--tp-muted); font: 500 20px/1 var(--font-display); text-align: center; }
.action-summary { display: flex; min-width: 0; min-height: 90px; align-items: center; justify-content: space-between; gap: 8px; padding: 16px 14px 16px 4px; border: 0; color: var(--tp-ink); background: transparent; text-align: left; }
.action-summary span { min-width: 0; }
.action-summary strong,
.action-summary small { display: block; }
.action-summary strong { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; line-height: 1.55; }
.pending-badge { display: inline-flex; margin-left: 5px; padding: 2px 5px; border-radius: 3px; color: #72501B; background: #F1DEBA; font-size: 11px; font-weight: 400; font-style: normal; vertical-align: 1px; }
.action-summary small { margin-top: 7px; color: var(--tp-muted); font-size: 12px; line-height: 1.7; font-variant-numeric: tabular-nums; }
.action-summary > b { flex-shrink: 0; color: var(--tp-muted); font-size: 11px; font-weight: 400; }
.action-summary > b span { margin-left: 3px; }
.order-controls { display: flex; align-items: center; gap: 8px; }
.order-controls span { margin-right: auto; color: var(--tp-muted); font-size: 12px; }
.order-controls button { min-height: 44px; padding: 0 12px; border: 1px solid var(--tp-line); border-radius: 6px; color: var(--tp-muted); background: transparent; }
.order-controls button:disabled { opacity: .22; }
.card-validation { grid-column: 1 / -1; padding: 10px 14px; border-top: 1px solid rgb(179 38 30 / 20%); color: var(--tp-danger); background: rgb(179 38 30 / 5%); }
.card-validation strong { font-size: 12px; }
.card-validation p { margin: 3px 0 0; font-size: 12px; }

.empty-plan { margin-top: 22px; padding: 38px 22px; text-align: center; }
.empty-plan > span { color: var(--tp-primary); font: 700 26px/1 var(--font-display); }
.empty-plan h2 { margin: 8px 0; }
.empty-plan p { max-width: 330px; margin: auto; color: var(--tp-muted); font-size: 13px; line-height: 1.7; }
.empty-plan button { min-height: 48px; margin-top: 18px; padding: 0 20px; border: 0; border-radius: 999px; color: var(--tp-on-primary); background: var(--tp-primary); font-weight: 800; }
.empty-plan small { display: block; margin-top: 9px; color: var(--tp-muted); font-size: 11px; }
.empty-plan .empty-plan-error { margin-top: 10px; color: var(--tp-danger); }

.plan-tools { display: flex; flex-wrap: wrap; align-items: start; justify-content: space-between; gap: 12px; margin-top: 14px; }
.manual-trigger { display: flex; min-height: 48px; align-items: center; gap: 8px; padding: 0 6px; border: 0; border-radius: 6px; color: var(--tp-muted); background: transparent; text-align: left; font-size: 12px; }
.manual-trigger > span:first-child { font-size: 20px; }
.manual-trigger strong,
.manual-trigger small { display: block; }
.manual-trigger small { display: none; }
.manual-trigger strong { font-weight: 500; }
.manual-form { display: grid; width: 100%; gap: 14px; padding: 18px; }
.manual-form h2 { margin: 5px 0 0; }
.manual-form label,
.save-as-panel label,
.field-stack { display: grid; gap: 6px; color: var(--tp-muted); font-size: 12px; }
.manual-form input,
.save-as-panel input,
.field-stack input { min-height: 48px; padding: 11px 13px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: var(--tp-surface); }
.mode-toggle { display: inline-flex; justify-self: start; gap: 4px; padding: 4px; border: 1px solid var(--tp-line); border-radius: 13px; background: #F1F6FA; }
.mode-toggle button { min-height: 44px; padding: 0 14px; border: 0; border-radius: 9px; color: var(--tp-muted); background: transparent; }
.mode-toggle button.active { color: var(--tp-surface); background: var(--tp-ink); font-weight: 800; }
.manual-actions,
.save-as-panel form > div { display: flex; justify-content: flex-end; gap: 8px; }
.manual-actions button,
.save-as-panel button { min-height: 44px; padding: 0 15px; border: 1px solid var(--tp-line); border-radius: 999px; color: var(--tp-muted); background: var(--tp-surface); }
.manual-actions .confirm,
.save-as-panel .confirm { color: var(--tp-on-primary); border-color: var(--tp-primary); background: var(--tp-primary); font-weight: 800; }
.save-as-panel { display: grid; gap: 10px; padding: 0; }
.save-as-panel > button { border: 0; background: transparent; font-size: 12px; }
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

.start-training-panel { position: fixed; right: 0; bottom: 0; left: 0; z-index: 20; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px max(16px, env(safe-area-inset-right)) max(14px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); border-top: 1px solid var(--tp-line); color: var(--tp-ink); background: var(--tp-surface); }
.start-training-panel strong,
.start-training-panel small { display: block; }
.start-training-panel strong { font-size: 13px; }
.start-training-panel small { max-width: 40em; margin-top: 5px; color: var(--tp-muted); font-size: 11px; line-height: 1.6; }
.start-training-panel button,
.start-training-panel a { display: grid; min-height: 48px; flex: 0 0 auto; place-items: center; padding: 0 24px; border: 0; border-radius: 7px; color: var(--tp-on-primary); background: var(--tp-primary); font-weight: 600; text-decoration: none; }
.start-training-panel button:disabled { opacity: .55; }

.sheet-backdrop { position: fixed; inset: 0; z-index: 60; width: 100%; border: 0; background: rgb(14 19 17 / 30%); animation: backdrop-in 180ms ease; }
.action-sheet { position: fixed; right: 0; bottom: 0; left: 0; z-index: 61; display: grid; align-content: start; max-height: 92dvh; gap: 20px; overflow-y: auto; overscroll-behavior: contain; padding: 22px clamp(16px, 4vw, 32px) 0; border-radius: 18px 18px 0 0; color: var(--tp-ink); background: var(--tp-surface); box-shadow: -12px 0 60px rgb(14 19 17 / 12%); animation: sheet-in 200ms ease; }
.action-sheet > header { display: flex; align-items: center; justify-content: space-between; }
.action-sheet h2 { margin: 6px 0 0; font-size: 24px; font-weight: 600; }
.action-sheet > header button { width: 44px; min-height: 44px; border: 0; border-radius: 6px; color: var(--tp-muted); background: transparent; font-size: 25px; }
.user-priority-note { margin: 0; padding: 11px 13px; border-left: 3px solid var(--tp-secondary); color: var(--tp-muted); background: rgb(25 205 192 / 12%); font-size: 12px; line-height: 1.6; }
.confirmation-note { margin: 0; padding: 12px 13px; border: 1px solid #DDBD85; border-radius: 13px; color: #72501B; background: #FFF7E8; font-size: 12px; line-height: 1.6; }
.plan-name { font-size: 17px; font-weight: 800; }
.parameter-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.parameter-grid label { display: grid; gap: 6px; }
.parameter-grid label > span { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px; color: var(--tp-muted); font-size: 11px; }
.parameter-grid em,
.segment-line em { color: var(--tp-primary-readable); font-size: 11px; font-style: normal; }
.parameter-grid input { width: 100%; min-height: 48px; padding: 10px 12px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: #F1F6FA; font: 700 19px/1 var(--font-display); }
.source-detail { display: grid; gap: 10px; padding: 15px; border: 1px solid var(--tp-line); border-radius: 16px; background: #F1F6FA; }
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
.sheet-actions { position: sticky; bottom: 0; display: flex; flex-wrap: wrap; gap: 8px; margin: 0 calc(-1 * clamp(16px, 4vw, 32px)); padding: 16px clamp(16px, 4vw, 32px) max(16px, env(safe-area-inset-bottom)); border-top: 1px solid var(--tp-line); background: var(--tp-surface); }
.sheet-actions button { min-height: 46px; padding: 0 14px; border: 1px solid var(--tp-line); border-radius: 6px; color: var(--tp-muted); background: transparent; font-size: 13px; }
.sheet-actions .danger { color: var(--tp-danger); }
.sheet-actions .done { margin-left: auto; color: var(--tp-on-primary); border-color: var(--tp-primary); background: var(--tp-primary); font-weight: 800; }
.adjustment-intents { display: grid; gap: 8px; }
.adjustment-safety-stop { display: flex; min-height: 48px; align-items: center; gap: 9px; padding: 8px 0; border: 0; border-radius: 6px; color: var(--tp-muted); background: transparent; font-size: 12px; line-height: 1.6; text-align: left; }
.adjustment-safety-stop[aria-pressed="true"] { color: var(--tp-surface); border-color: var(--tp-danger); background: var(--tp-danger); }
.adjustment-intents button { position: relative; display: grid; min-height: 78px; gap: 6px; padding: 16px 38px 16px 16px; border: 1px solid var(--tp-line); border-radius: 8px; color: var(--tp-ink); background: transparent; text-align: left; }
.adjustment-intents button::after { position: absolute; right: 16px; top: 20px; content: ''; width: 14px; height: 14px; border: 1px solid var(--tp-line); border-radius: 50%; }
.adjustment-intents button.active { color: var(--tp-ink); border-color: var(--tp-ink); background: var(--tp-surface-raised); }
.adjustment-intents button.active::after { border: 4px solid var(--tp-ink); }
.adjustment-intents strong { font-size: 15px; font-weight: 600; }
.adjustment-intents strong,
.adjustment-intents span { display: block; }
.adjustment-intents span { color: var(--tp-muted); font-size: 11px; line-height: 1.45; }
.adjustment-intents button.active span { color: var(--tp-muted); }
.generate-adjustment { min-height: 48px; border: 1px solid var(--tp-primary); border-radius: 999px; color: var(--tp-on-primary); background: var(--tp-primary); font-weight: 800; }
.adjustment-change-list { display: grid; gap: 22px; }
.adjustment-change-list article { display: grid; gap: 12px; padding-bottom: 18px; border-bottom: 1px solid var(--tp-line); }
.adjustment-change-list h3 { margin: 0; font-size: 15px; font-weight: 600; }
.change-row > .numeric-field { display: grid; gap: 8px; }
.adjustment-change-list article span,
.adjustment-change-list article p { color: var(--tp-muted); font-size: 11px; }
.adjustment-change-list article b { color: var(--tp-ink); font: 500 18px/1.4 var(--font-display), var(--font-cn); font-variant-numeric: tabular-nums; }
.adjustment-change-list article p,
.adjustment-dialog-notice { margin: 0; line-height: 1.55; }
.adjustment-dialog-notice { color: var(--tp-success); font-size: 12px; }
.context-stale-note { margin: 0; padding: 10px 12px; border-left: 3px solid #9A6A1D; color: #72501B; background: #FFF7E8; font-size: 11px; line-height: 1.55; }
.inline-error { margin: 0; color: var(--tp-danger); font-size: 12px; }
.adjustment-intro { margin: 4px 0 0; font-size: 15px; }
.preview-heading { margin: 4px 0 0; font-size: 18px; font-weight: 500; }
.adjustment-impact { display: grid; gap: 8px; margin: 0; padding: 18px; border-radius: 8px; background: var(--tp-surface-raised); }
.adjustment-impact span, .adjustment-impact small { color: var(--tp-muted); font-size: 12px; }
.adjustment-impact b { font-size: 20px; font-weight: 500; font-variant-numeric: tabular-nums; }
.plan-page button, .plan-page a { transition: background-color 160ms ease, color 160ms ease, transform 160ms ease; }
.plan-readiness { min-width: 0; padding-block: 12px; border-block: 1px solid var(--tp-line); }
.plan-readiness h2 { margin: 0; font-size: 14px; font-weight: 600; }
.plan-readiness p { margin: 6px 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.pending-review-list { margin: 8px 0 0; padding: 0; list-style: none; }
.pending-review-list button { display: flex; width: 100%; min-height: 44px; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 0; border: 0; color: var(--tp-ink); background: transparent; text-align: left; }
.pending-review-list button span:first-child { min-width: 0; overflow-wrap: anywhere; }
.pending-review-list button span:last-child { flex-shrink: 0; color: var(--tp-primary-readable); font-size: 12px; }
.workout-guide summary { display: flex; min-height: 44px; align-items: center; color: var(--tp-muted); font-size: 12px; cursor: pointer; }
.plan-page button:active:not(:disabled) { transform: translateY(1px); }
@media (hover: hover) {
  .plan-card:hover { border-color: #9BAEBE; }
  .back-link:hover, .coach-actions button:hover, .coach-actions a:hover { background: rgb(28 40 34 / 5%); }
  .start-training-panel button:hover { background: #31D7CB; }
}
@keyframes sheet-in { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
@keyframes backdrop-in { from { opacity: 0; } to { opacity: 1; } }

@media (min-width: 1024px) {
  .plan-page { padding: 32px 44px calc(132px + env(safe-area-inset-bottom)); }
  .plan-header { grid-template-columns: 44px auto 1fr; gap: 8px; margin-bottom: 44px; }
  .plan-header > span { text-align: left; }
  .plan-workspace { grid-template-columns: minmax(0, 1fr); gap: 28px; }
  .plan-overview { display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, .85fr); gap: 16px 32px; align-items: start; }
  .plan-hero { align-self: center; padding-bottom: 0; border: 0; }
  .plan-readiness { grid-column: 1; }
  .coach-card { grid-column: 2; grid-row: 1 / span 3; margin-top: 0; }
  .coach-card.has-floating-coach .coach-copy { width: calc(100% - 112px); min-height: 132px; }
  .coach-actions { align-items: start; }
  .plan-metrics { gap: 12px 18px; }
  .plan-metrics span:last-child { flex-basis: auto; }
  .action-summary { min-height: 104px; padding-right: 24px; }
  .action-sheet { top: 0; right: 0; bottom: 0; left: auto; width: min(500px, 48vw); max-height: none; border-radius: 0; gap: 24px; }
  .start-training-panel { left: var(--tp-sidebar-width, 216px); padding: 18px 44px; }
}

@media (max-width: 359px) {
  .parameter-grid { grid-template-columns: 1fr; }
  .start-training-panel { align-items: stretch; }
  .start-training-panel > div { display: none; }
  .start-training-panel button,
  .start-training-panel a { width: 100%; }
}

.plan-workspace { gap: 20px; }
.plan-header { margin-bottom: 12px; }
.plan-hero { padding-bottom: 14px; }
.plan-metrics { gap: 8px 14px; }
.plan-metrics b { font-size: 20px; }
.coach-card { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 4px 12px; margin-top: 16px; padding: 8px 14px; border: 0; }
.coach-card h2 { font-size: 14px; }
.coach-actions button { font-size: 14px; }
.inline-number-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.inline-number-grid > :last-child { grid-column: 1 / -1; display: grid; grid-template-columns: 1fr minmax(150px, 180px); align-items: center; padding-top: 12px; border-top: 1px solid var(--tp-line); }
.action-summary { min-height: 76px; padding-top: 12px; padding-bottom: 12px; }
.action-summary strong { font-size: 16px; }
.action-summary small { margin-top: 4px; font-size: 13px; }
.action-index { color: var(--tp-ink); font-size: 24px; }
.quick-notice { padding: 0; border: 0; background: transparent; }
@media (max-width: 359px) {
  .inline-number-grid { grid-template-columns: 1fr; }
  .inline-number-grid > .numeric-field { grid-template-columns: minmax(0, 1fr) 160px; align-items: center; }
}
@media (max-width: 767px) {
  .start-training-panel { display: grid; grid-template-columns: 1fr; gap: 6px; }
  .start-training-panel > div > strong { display: none; }
  .start-training-panel > div { order: 2; text-align: center; }
  .start-training-panel button, .start-training-panel a { width: 100%; min-height: 52px; border-radius: 12px; }
  .start-training-panel small { margin-top: 0; }
}

</style>
