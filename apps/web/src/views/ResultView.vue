<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import CoachMotion from '@/features/experience/CoachMotion.vue'
import {
  deliverCompletionPoster,
  downloadCompletionPoster,
  renderCompletionPoster,
} from '@/features/experience/poster'
import { useLibraryStore } from '@/stores/library'
import { useDraftStore } from '@/stores/draft'
import { useTrainingStore } from '@/stores/training'

const route = useRoute()
const router = useRouter()
const library = useLibraryStore()
const draft = useDraftStore()
const training = useTrainingStore()
const pending = ref(false)
const feedback = ref('')
const desktopQuery = typeof window.matchMedia === 'function'
  ? window.matchMedia('(min-width: 1024px)') : null
const detailsDefaultOpen = ref(desktopQuery?.matches ?? false)
const updateDetailsDefault = (event: MediaQueryListEvent): void => {
  detailsDefaultOpen.value = event.matches
}
onMounted(() => desktopQuery?.addEventListener('change', updateDetailsDefault))
onBeforeUnmount(() => desktopQuery?.removeEventListener('change', updateDetailsDefault))
const recordId = computed(() => String(route.params.recordId ?? ''))
const record = computed(() => library.records.find((entry) => entry.id === recordId.value) ?? null)
const canCreatePoster = computed(() => record.value?.outcome === 'completed')
const primaryActionLabel = computed(() => training.hasCurrent ? '继续当前训练' : '再练一次')
const resultDateLabel = computed(() => {
  const endedAt = record.value?.endedAt
  if (!endedAt) return ''
  const date = new Date(endedAt)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(date)
})
const coachResultMessage = computed(() => (
  record.value?.outcome === 'completed'
    ? '这一页已经替你记好了。下一次训练，TrainPal 还会从这里继续陪你。'
    : '今天做到这里也算一次真实训练。实际完成量已经保存，不需要勉强补齐。'
))
const coachCue = computed(() => (
  record.value?.outcome === 'completed' && record.value.coachStyleId
    ? { sequence: 1, event: 'session_completed' as const }
    : null
))

const formatDuration = (seconds: number): string => {
  const rounded = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(rounded / 60)
  const remainder = rounded % 60
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}

const posterBlob = async (): Promise<Blob> => {
  const current = record.value
  if (!current) throw new Error('训练记录不存在')
  return renderCompletionPoster({
    outcome: current.outcome,
    planName: current.plan.name,
    trainingDurationSeconds: current.trainingDurationSeconds,
    caloriesKcal: current.calorie.value,
    completedActionCount: current.completedActionCount,
    coachStyleId: current.coachStyleId,
  })
}

const sharePoster = async (): Promise<void> => {
  if (pending.value || !record.value) return
  pending.value = true
  feedback.value = '正在生成海报…'
  try {
    const result = await deliverCompletionPoster(await posterBlob(), record.value.plan.name)
    feedback.value = result === 'shared' ? '已打开分享' : result === 'downloaded' ? '海报已下载' : '已取消分享'
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '海报生成失败，请重试'
  } finally {
    pending.value = false
  }
}

const downloadPoster = async (): Promise<void> => {
  if (pending.value || !record.value) return
  pending.value = true
  feedback.value = '正在生成海报…'
  try {
    downloadCompletionPoster(await posterBlob(), record.value.plan.name)
    feedback.value = '海报已下载'
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '海报生成失败，请重试'
  } finally {
    pending.value = false
  }
}

const trainAgain = async (): Promise<void> => {
  const current = record.value
  if (!current || pending.value) return
  if (training.hasCurrent) {
    await router.push('/training')
    return
  }

  const alreadyCurrent = draft.plan.name === current.plan.name
    && JSON.stringify(draft.items) === JSON.stringify(current.plan.items)
  if (alreadyCurrent) {
    await router.push('/plan')
    return
  }
  if (
    draft.items.length > 0
    && !window.confirm('再练一次会替换当前方案，确定继续吗？')
  ) return

  pending.value = true
  feedback.value = ''
  try {
    await draft.flushPersist()
    await draft.quiescePersistence()
    draft.adoptPersistedPlan(await library.replaceCurrentDraft({
      name: current.plan.name,
      items: current.plan.items,
    }))
    await router.push('/plan')
  } catch {
    feedback.value = '这次训练方案没有恢复成功，请重试'
  } finally {
    draft.resumePersistence()
    pending.value = false
  }
}

onMounted(async () => {
  try {
    await library.refreshHistory()
  } catch {
    feedback.value = '训练记录暂时没有读取成功，请返回重试'
  }
})
</script>

<template>
  <main class="result-page">
    <header class="result-header">
      <RouterLink to="/mine">← 我的训练</RouterLink>
      <span>{{ resultDateLabel || '训练记录' }}</span>
    </header>

    <section v-if="record" class="result-card" :class="{ completed: record.outcome === 'completed' }">
      <div class="result-hero">
        <div>
          <p class="eyebrow">TrainPal · 训练记录</p>
          <h1>{{ record.outcome === 'completed' ? '练完啦' : '今天先到这里' }}</h1>
          <h2>{{ record.plan.name }}</h2>
        </div>
        <span class="outcome-stamp">{{ record.outcome === 'completed' ? '完整完成' : '实际完成' }}</span>
      </div>

      <div class="coach-result">
        <CoachMotion
          :style-id="record.coachStyleId"
          :state="record.outcome === 'completed' ? 'completed' : 'paused'"
          :cue="coachCue"
          :visible="library.preferences.petVisible"
        />
        <div>
          <span>TrainPal 留言</span>
          <p>{{ coachResultMessage }}</p>
        </div>
      </div>

      <div class="result-metrics" aria-label="本次训练数据">
        <span><b>{{ formatDuration(record.trainingDurationSeconds) }}</b>训练时长</span>
        <span><b>约 {{ record.calorie.value }}</b>千卡</span>
        <span><b>{{ record.completedActionCount }}</b>完成动作</span>
      </div>

      <details class="action-results" :open="detailsDefaultOpen">
        <summary>
          <span>本次完成内容</span>
          <small>{{ record.actions.length }} 个动作 · 查看明细</small>
        </summary>
        <div v-for="action in record.actions" :key="action.itemId" class="action-result-row">
          <span><strong>{{ action.name }}</strong><small>{{ action.completedSets }} / {{ action.targetSets }} 组</small></span>
          <b>{{ action.status === 'completed' ? '完成' : action.status === 'partial' ? '部分完成' : '已跳过' }}</b>
        </div>
      </details>

      <p v-if="!canCreatePoster" class="early-note">提前结束只保留实际记录，不生成完成海报。</p>
      <div v-if="canCreatePoster" class="poster-actions" aria-label="训练结果分享">
        <button type="button" :disabled="pending" @click="sharePoster">分享海报</button>
        <button type="button" class="secondary" :disabled="pending" @click="downloadPoster">下载海报</button>
      </div>
      <p v-if="feedback" class="feedback" role="status">{{ feedback }}</p>

      <footer>
        <button type="button" :disabled="pending" @click="trainAgain">{{ primaryActionLabel }}</button>
        <RouterLink to="/">继续找动作</RouterLink>
      </footer>
    </section>

    <section v-else class="missing-result">
      <h1>没有找到这条训练记录</h1>
      <p>这条记录可能已经从本机清除。</p>
      <RouterLink to="/mine">返回我的训练</RouterLink>
    </section>
  </main>
</template>

<style scoped>
.result-page {
  width: min(100%, 1120px);
  min-height: 100dvh;
  margin: auto;
  padding:
    max(18px, env(safe-area-inset-top))
    clamp(16px, 4vw, 28px)
    max(34px, env(safe-area-inset-bottom));
  color: var(--tp-ink);
  background: transparent;
}
.result-header,
.result-metrics,
.poster-actions,
.result-card footer { display: flex; align-items: center; }
.result-header { justify-content: space-between; }
.result-header a { display: inline-grid; min-height: 44px; place-items: center; color: var(--tp-ink); font-size: 12px; font-weight: 800; text-decoration: none; }
.result-header span { color: var(--tp-muted); font-size: 11px; }
.result-card { position: relative; margin-top: 20px; padding: clamp(22px, 6vw, 42px); border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-surface); box-shadow: var(--tp-shadow-soft); }
.result-hero { position: relative; display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.eyebrow { margin: 0; color: var(--tp-primary-readable); font: 700 11px/1 var(--font-display), var(--font-cn); letter-spacing: .14em; }
.result-card h1 { margin: 12px 0 6px; color: var(--tp-ink); font: 700 clamp(32px, 8vw, 44px)/1.3 var(--font-display), var(--font-cn); letter-spacing: -.03em; }
.result-card h2 { margin: 0; color: var(--tp-muted); font-size: 14px; font-weight: 700; }
.outcome-stamp { flex: 0 0 auto; padding: 10px 8px; border: 1px solid var(--tp-line); border-radius: 8px; color: var(--tp-primary-readable); font-size: 11px; font-weight: 900; letter-spacing: .08em; }
.result-card.completed .outcome-stamp { border-color: var(--tp-success); color: var(--tp-success); }
.coach-result { position: relative; display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 14px; margin: 24px 0 12px; padding: 14px 16px; border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-surface-raised); }
.coach-result :deep(.trainpal-coach__image) { width: clamp(72px, 22vw, 104px); filter: drop-shadow(0 9px 18px rgb(42 51 45 / 20%)); }
.coach-result span { color: var(--tp-success); font: 700 11px/1 var(--font-display), var(--font-cn); letter-spacing: .12em; }
.coach-result p { margin: 7px 0 0; color: var(--tp-ink); font-size: 13px; line-height: 1.6; }
.result-metrics { position: relative; justify-content: space-between; gap: 8px; margin: 18px 0; padding: 18px 0; border-block: 1px solid var(--tp-line); }
.result-metrics span { flex: 1; color: var(--tp-muted); font-size: 11px; text-align: center; }
.result-metrics b { display: block; margin-bottom: 5px; color: var(--tp-ink); font: 700 clamp(28px, 9vw, 40px)/.9 var(--font-display); }
.action-results { position: relative; border-bottom: 1px solid var(--tp-line); }
.action-results summary { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 12px; color: var(--tp-ink); font-size: 13px; font-weight: 800; cursor: pointer; list-style: none; }
.action-results summary::-webkit-details-marker { display: none; }
.action-results summary::after { content: '＋'; color: var(--tp-primary); font-size: 18px; }
.action-results[open] summary::after { content: '－'; }
.action-results summary small { margin-left: auto; color: var(--tp-muted); font-size: 11px; font-weight: 500; }
.action-result-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 0; border-top: 1px solid var(--tp-line); }
.action-result-row strong,
.action-result-row small { display: block; }
.action-result-row small { margin-top: 3px; color: var(--tp-muted); font-size: 11px; }
.action-result-row > b { color: var(--tp-success); font-size: 11px; }
.poster-actions { position: relative; gap: 8px; margin-top: 18px; }
.poster-actions button { min-height: 48px; flex: 1; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: var(--tp-surface); font-weight: 800; }
.poster-actions .secondary { color: var(--tp-muted); background: transparent; }
.early-note,
.feedback { position: relative; color: var(--tp-muted); font-size: 11px; text-align: center; }
.early-note { margin: 18px 0 0; padding: 12px; border-radius: 14px; background: rgb(108 116 110 / 8%); line-height: 1.55; }
.feedback { color: var(--tp-success); }
.result-card footer { position: relative; flex-direction: column; justify-content: center; gap: 6px; margin-top: 22px; }
.result-card footer button { display: inline-grid; width: 100%; min-height: 52px; padding: 0 20px; place-items: center; border: 1px solid var(--tp-primary); border-radius: 12px; color: var(--tp-surface); background: var(--tp-primary-readable); box-shadow: var(--tp-shadow-soft); font-weight: 900; }
.result-card footer a { display: inline-grid; min-width: 44px; min-height: 44px; padding: 0 6px; place-items: center; color: var(--tp-muted); font-size: 11px; font-weight: 700; text-decoration: none; }
.result-card footer button:disabled { opacity: .5; }
.missing-result { margin-top: 20vh; padding: 28px; border: 1px solid var(--tp-line); border-radius: 28px; background: var(--tp-surface); text-align: center; box-shadow: var(--tp-shadow-soft); }
.missing-result h1 { margin-bottom: 8px; font: 700 36px/.95 var(--font-display), var(--font-cn); }
.missing-result p { color: var(--tp-muted); font-size: 12px; }
.missing-result a { display: inline-grid; min-height: 44px; place-items: center; color: var(--tp-primary-readable); font-weight: 800; }

@media (max-width: 359px) {
  .result-hero { display: grid; }
  .outcome-stamp { position: absolute; top: 0; right: 0; }
  .result-metrics { gap: 3px; }
  .result-metrics b { font-size: 26px; }
  .coach-result { grid-template-columns: 1fr; justify-items: center; text-align: center; }
}
@media (min-width: 1024px) {
  .result-card { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px 40px; }
  .result-hero { grid-column: 1 / -1; margin-bottom: 8px; }
  .coach-result { grid-column: 1; margin: 0; }
  .result-metrics { grid-column: 1; margin: 0; }
  .action-results { grid-column: 2; grid-row: 2 / span 3; align-self: start; }
  .poster-actions, .early-note, .feedback { grid-column: 1; margin-top: 0; }
  .result-card footer { grid-column: 1 / -1; flex-direction: row; padding-top: 12px; border-top: 1px solid var(--tp-line); }
  .result-card footer button { width: auto; min-width: 220px; }
}
</style>
