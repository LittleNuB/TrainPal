<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import TopLevelNav from '@/components/TopLevelNav.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { useAppBootstrapStore } from '@/stores/app-bootstrap'
import { useTrainingStore } from '@/stores/training'

const route = useRoute()
const analysis = useAnalysisStore()
const bootstrap = useAppBootstrapStore()
const training = useTrainingStore()
const nowMilliseconds = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | null = null

const isTrainingTheme = computed(() => route.meta.theme === 'training')
const showBottomNav = computed(() => route.meta.showBottomNav === true)
const showSidebar = computed(() => !isTrainingTheme.value && !route.path.startsWith('/__design'))
const showTrainingTask = computed(() => (
  training.hasCurrent && route.meta.showTrainingTask === true
))
const showAnalysisTask = computed(() => (
  (analysis.isRunning || analysis.hasOwnedRun)
  && route.meta.showAnalysisTask === true
))
const taskCount = computed(() => Number(showAnalysisTask.value) + Number(showTrainingTask.value))
const taskDockStyle = computed(() => ({
  '--tp-task-reserve': taskCount.value
    ? `${taskCount.value * 58 + Math.max(0, taskCount.value - 1) * 8 + 16}px`
    : '0px',
}))

const continueLabel = computed(() => {
  const current = training.session
  if (!current) return ''
  if (current.status === 'resting' && current.restEndsAt) {
    const remaining = Math.max(0, Math.ceil(
      (Date.parse(current.restEndsAt) - nowMilliseconds.value) / 1_000,
    ))
    if (remaining === 0) return '休息结束 · 准备继续'
    return `休息中 · ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`
  }
  if (current.status === 'ready_to_continue') return '准备继续训练'
  return '继续训练'
})

const analysisLabel = computed(() => (
  analysis.isRunning ? analysis.stageLabel : '查看分析结果'
))

watch(
  [() => route.fullPath, () => bootstrap.status],
  async () => {
    const routeTitle = typeof route.meta.title === 'string' ? route.meta.title : ''
    document.title = routeTitle ? `${routeTitle} · TrainPal` : 'TrainPal'
    if (bootstrap.status !== 'ready') return
    await nextTick()
    const heading = document.querySelector<HTMLElement>('.app-shell main h1')
    if (!heading) return
    heading.tabIndex = -1
    heading.focus({ preventScroll: true })
  },
  { immediate: true },
)

onMounted(() => {
  ticker = setInterval(() => { nowMilliseconds.value = Date.now() }, 1_000)
})

onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'tp-training-theme app-shell--training': isTrainingTheme,
      'app-shell--has-task-dock': taskCount > 0,
      'app-shell--sidebar': showSidebar,
    }"
    :data-theme="isTrainingTheme ? 'training' : 'journal'"
    :style="taskDockStyle"
  >
    <main v-if="bootstrap.status !== 'ready'" class="bootstrap-shell">
      <template v-if="bootstrap.status === 'loading'">
        <p class="bootstrap-eyebrow">TrainPal</p>
        <h1>正在准备你的训练</h1>
        <p>方案、训练进度和记录只保存在当前设备。</p>
      </template>
      <section v-else role="alert" aria-live="assertive">
        <p class="bootstrap-eyebrow">TrainPal</p>
        <h1>本机训练数据暂时无法读取</h1>
        <p>数据没有被清除，可以重新尝试读取。</p>
        <button class="tp-primary-action" type="button" @click="bootstrap.retry">重试读取</button>
        <div class="bootstrap-product-summary" aria-label="TrainPal 产品介绍">
          <p>TRAINPAL 是什么</p>
          <span>把本地健身视频转成可确认、可调整、可完成的训练。</span>
        </div>
      </section>
    </main>

    <template v-else>
      <RouterView />

      <aside
        v-if="showAnalysisTask || showTrainingTask"
        class="global-task-dock"
        :class="{
          'global-task-dock--with-nav': showBottomNav,
          'global-task-dock--above-action': route.meta.taskDockAboveAction === true,
        }"
        aria-label="进行中的任务"
      >
        <RouterLink v-if="showAnalysisTask" class="global-analysis-entry" to="/analysis">
          <span>
            <small>TRAINPAL 正在工作</small>
            {{ analysisLabel }}
          </span>
          <b aria-hidden="true">→</b>
        </RouterLink>
        <RouterLink v-if="showTrainingTask" class="global-training-entry" to="/training">
          <span>
            <small>当前训练</small>
            {{ continueLabel }}
          </span>
          <b aria-hidden="true">→</b>
        </RouterLink>
      </aside>

      <TopLevelNav
        v-if="showBottomNav || showSidebar"
        :desktop-only="!showBottomNav"
        :analysis-label="showAnalysisTask ? analysisLabel : undefined"
        :training-label="showTrainingTask ? continueLabel : undefined"
      />
    </template>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100dvh;
  color: var(--tp-ink);
}

.app-shell--training {
  min-height: 100dvh;
  color: var(--tp-training-ink);
  background: var(--tp-training-canvas);
}

.bootstrap-shell {
  display: grid;
  min-height: 100dvh;
  place-content: center;
  gap: 14px;
  padding: 28px;
  text-align: center;
}

.bootstrap-shell section { display: grid; gap: 14px; }
.bootstrap-eyebrow { margin: 0; color: var(--tp-primary-readable); font: 700 12px/1 var(--font-display); letter-spacing: .16em; }
.bootstrap-shell h1 { max-width: 390px; margin: 0; color: var(--tp-ink); font: 700 clamp(42px, 12vw, 68px)/.92 var(--font-display), var(--font-cn); }
.bootstrap-shell p:not(.bootstrap-eyebrow) { max-width: 330px; margin: 0 auto; color: var(--tp-muted); font-size: 14px; line-height: 1.7; }
.bootstrap-shell button { justify-self: center; margin-top: 8px; }
.bootstrap-product-summary { display: grid; gap: 6px; max-width: 390px; margin: 8px auto 0; padding: 15px; border: 1px solid var(--tp-line); border-radius: 16px; color: var(--tp-ink); background: rgb(255 253 248 / 68%); text-align: left; }
.bootstrap-product-summary p { margin: 0; color: var(--tp-primary-readable); font: 700 11px/1 var(--font-display); letter-spacing: .12em; }
.bootstrap-product-summary span { color: var(--tp-muted); font-size: 12px; line-height: 1.6; }

.global-task-dock {
  position: fixed;
  right: max(14px, env(safe-area-inset-right));
  bottom: max(14px, env(safe-area-inset-bottom));
  left: max(14px, env(safe-area-inset-left));
  z-index: 55;
  display: grid;
  width: min(calc(100% - 28px), 430px);
  margin: 0 auto;
  gap: 8px;
  pointer-events: none;
}

.global-task-dock--with-nav { bottom: calc(88px + env(safe-area-inset-bottom)); }

.global-task-dock--above-action { bottom: calc(94px + env(safe-area-inset-bottom)); }

.global-analysis-entry,
.global-training-entry {
  display: flex;
  min-height: 58px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 9px 14px 9px 16px;
  border: 1px solid rgb(28 40 34 / 15%);
  border-radius: 18px;
  color: var(--tp-training-ink);
  background: var(--tp-surface-raised);
  box-shadow: var(--tp-shadow-float);
  font-size: 13px;
  font-weight: 800;
  text-decoration: none;
  pointer-events: auto;
  backdrop-filter: blur(16px);
}

.global-analysis-entry small,
.global-training-entry small {
  display: block;
  margin-bottom: 3px;
  color: var(--tp-secondary);
  font: 700 11px/1 var(--font-display);
  letter-spacing: .1em;
  text-transform: uppercase;
}

.global-analysis-entry b,
.global-training-entry b {
  color: var(--tp-secondary);
  font-size: 22px;
}

@media (min-width: 1024px) {
  .app-shell--sidebar { --tp-sidebar-width: 216px; padding-left: var(--tp-sidebar-width); }
  .app-shell--sidebar .global-task-dock { display: none; }
  .app-shell--sidebar :deep(.tp-page) { --tp-task-reserve: 0px; }
  .global-task-dock,
  .global-task-dock--with-nav {
    right: 24px;
    bottom: 106px;
    left: auto;
    width: 360px;
  }
}

</style>
