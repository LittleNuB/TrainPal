<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

import { estimatePlanMinutes } from '@/domain/plan'
import { QUICK_EXPERIENCE_LABEL } from '@/features/quick-experience/fixture'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useTrainingStore } from '@/stores/training'

const router = useRouter()
const draft = useDraftStore()
const library = useLibraryStore()
const training = useTrainingStore()
const pending = ref(false)
const notice = ref('')
const showAllPlans = ref(false)

onBeforeRouteLeave(() => !pending.value)

const openEditor = async (): Promise<void> => {
  draft.resumePersistence()
  pending.value = false
  await router.push('/plan')
}

const currentPlanMinutes = computed(() => estimatePlanMinutes(draft.items))
const visiblePlans = computed(() => showAllPlans.value ? library.plans : library.plans.slice(0, 4))

const formatDate = (value: string): string => new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
}).format(new Date(value))

const prepareReplacement = async (message: string): Promise<boolean> => {
  if (pending.value) return false
  if (draft.items.length > 0 && !window.confirm(message)) return false
  pending.value = true
  notice.value = ''
  try {
    await draft.flushPersist()
    await draft.quiescePersistence()
    return true
  } catch {
    notice.value = '当前修改还没保存成功，请重试后再切换方案'
    pending.value = false
    return false
  }
}

const useQuickPlan = async (): Promise<void> => {
  if (!await prepareReplacement('切换到快速体验方案？当前方案会保留在方案库。')) return
  try {
    draft.adoptPersistedPlan(await library.useQuickExperience())
    await openEditor()
  } catch {
    notice.value = '快速体验方案没有载入成功，请重试'
  } finally {
    draft.resumePersistence()
    pending.value = false
  }
}

const openPlan = async (planId: string): Promise<void> => {
  if (!await prepareReplacement('打开这个方案？当前方案会保留在方案库。')) return
  try {
    draft.adoptPersistedPlan(await library.openPlan(planId))
    await openEditor()
  } catch {
    notice.value = '这个方案没有打开成功，请重试'
  } finally {
    draft.resumePersistence()
    pending.value = false
  }
}

const deletePlan = async (planId: string, planName: string): Promise<void> => {
  const closingCurrent = draft.plan.linkedPlanId === planId ? '当前编辑也会关闭。' : ''
  if (pending.value || !window.confirm(`删除“${planName}”？${closingCurrent}已有训练记录会保留。`)) return
  pending.value = true
  notice.value = ''
  try {
    if (draft.plan.linkedPlanId === planId) await draft.flushPersist()
    await draft.quiescePersistence()
    const nextDraft = await library.deletePlan(planId)
    if (nextDraft) draft.adoptPersistedPlan(nextDraft)
    notice.value = '方案已删除，训练记录仍然保留'
  } catch {
    notice.value = '这个方案没有删除成功，请重试'
  } finally {
    draft.resumePersistence()
    pending.value = false
  }
}

const createPlan = async (): Promise<void> => {
  if (!await prepareReplacement('新建一个方案？当前方案会保留在方案库。')) return
  try {
    draft.adoptPersistedPlan(await library.replaceCurrentDraft({ name: '未命名方案', items: [] }))
    await openEditor()
  } catch {
    notice.value = '新方案没有创建成功，请重试'
  } finally {
    draft.resumePersistence()
    pending.value = false
  }
}

onMounted(async () => {
  try {
    await draft.flushPersist()
    await library.refreshHistory()
  } catch {
    notice.value = '训练方案暂时没有读取成功，请稍后重试'
  }
})
</script>

<template>
  <main class="train-hub tp-page">
    <header class="hub-heading">
      <div>
        <p class="tp-kicker">TODAY WITH TRAINPAL</p>
        <h1 class="tp-title">训练</h1>
      </div>
      <div class="day-stamp" aria-label="今天">
        <span>今天</span>
        <b>{{ new Date().getDate() }}</b>
      </div>
    </header>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="pending" class="notice" role="status">正在保存方案，请稍候…</p>

    <section v-if="training.hasCurrent" class="focus-card session-card tp-card">
      <p class="tp-kicker">CONTINUE</p>
      <h2>{{ training.session?.plan.name }}</h2>
      <p>训练进度已经留在本机。回来后仍由你决定何时开始下一组。</p>
      <div class="focus-stats">
        <span><b>{{ (training.session?.currentItemIndex ?? 0) + 1 }}</b> / {{ training.session?.plan.items.length ?? 0 }} 动作</span>
        <span>{{ training.session?.status === 'resting' ? '休息中' : '等待继续' }}</span>
      </div>
      <RouterLink class="tp-primary-action" to="/training">继续训练</RouterLink>
    </section>

    <section v-else-if="draft.items.length" class="focus-card current-plan tp-card">
      <p class="tp-kicker">CURRENT PLAN</p>
      <h2>{{ draft.plan.name }}</h2>
      <p>开始前，可以修改动作、组数和休息时间。</p>
      <div class="focus-stats">
        <span><b>{{ draft.items.length }}</b> 个动作</span>
        <span>约 {{ currentPlanMinutes || '—' }} 分钟</span>
      </div>
      <RouterLink class="tp-primary-action" to="/plan">检查并开始</RouterLink>
    </section>

    <section v-else class="focus-card empty-focus tp-card">
      <div class="empty-mark" aria-hidden="true">TP</div>
      <p class="tp-kicker">READY WHEN YOU ARE</p>
      <h2>先准备一场想练的训练</h2>
      <p>从首页导入自己的视频，或用清楚标记的体验方案走通训练闭环。</p>
      <button class="tp-primary-action" type="button" :disabled="pending" @click="useQuickPlan">
        {{ pending ? '正在准备…' : '使用快速体验方案' }}
      </button>
      <small>{{ QUICK_EXPERIENCE_LABEL }}不是 AI 分析结果。</small>
    </section>

    <section class="saved-section">
      <div class="section-title">
        <div>
          <h2>方案库 <span class="plan-count">{{ library.plans.length }}</span></h2>
        </div>
        <button class="new-plan" type="button" :disabled="pending" @click="createPlan">新建方案</button>
      </div>

      <p class="library-note">方案自动保存，仅此浏览器可用；清除浏览器数据会丢失。</p>

      <div v-if="library.plans.length" class="saved-list">
        <article v-for="plan in visiblePlans" :key="plan.id" class="saved-row">
          <button type="button" class="plan-open" :disabled="pending" @click="openPlan(plan.id)">
            <span>
              <strong>{{ plan.name }}</strong>
              <small>{{ plan.items.length }} 个动作 · 约 {{ estimatePlanMinutes(plan.items) || '—' }} 分钟 · {{ formatDate(plan.updatedAt) }}</small>
            </span>
            <b>打开</b>
          </button>
          <button type="button" class="plan-delete" :aria-label="`删除方案 ${plan.name}`" :disabled="pending" @click="deletePlan(plan.id, plan.name)">删除</button>
        </article>
      </div>
      <button
        v-if="library.plans.length > 4"
        type="button"
        class="show-all-plans"
        :aria-expanded="showAllPlans"
        @click="showAllPlans = !showAllPlans"
      >
        {{ showAllPlans ? '收起方案' : `查看全部 ${library.plans.length} 个方案` }}
      </button>
      <div v-if="!library.plans.length" class="saved-empty">
        <p>进入编辑的非空方案会自动保存在这里。</p>
        <RouterLink to="/">去导入视频</RouterLink>
      </div>
    </section>
  </main>
</template>

<style scoped>
.train-hub { display: grid; align-content: start; gap: 22px; }
.hub-heading { display: flex; align-items: end; justify-content: space-between; gap: 18px; padding: 14px 0 4px; }
.hub-heading .tp-title { margin-top: 7px; }
.day-stamp { display: grid; width: 66px; min-height: 76px; place-items: center; align-content: center; border: 1px solid var(--tp-line); border-radius: 18px 18px 18px 5px; background: var(--tp-surface); box-shadow: var(--tp-shadow-soft); transform: rotate(2deg); }
.day-stamp span { color: var(--tp-muted); font-size: 11px; }
.day-stamp b { margin-top: 2px; color: var(--tp-primary); font: 700 34px/1 var(--font-display); }
.notice { margin: 0; padding: 11px 13px; border-left: 3px solid var(--tp-secondary); border-radius: 0 10px 10px 0; color: var(--tp-success); background: rgb(165 186 99 / 12%); font-size: 12px; }

.focus-card { position: relative; display: grid; gap: 12px; overflow: hidden; padding: clamp(22px, 6vw, 34px); }
.focus-card::after { position: absolute; right: -46px; bottom: -65px; width: 170px; height: 170px; border: 24px solid rgb(165 186 99 / 18%); border-radius: 50%; content: ''; }
.focus-card > * { position: relative; z-index: 1; }
.focus-card h2 { max-width: 520px; margin: 0; font: 700 clamp(32px, 9vw, 48px)/.95 var(--font-display), var(--font-cn); }
.focus-card > p:not(.tp-kicker) { max-width: 520px; margin: 0; color: var(--tp-muted); font-size: 13px; line-height: 1.7; }
.focus-card .tp-primary-action { justify-self: start; margin-top: 4px; }
.focus-stats { display: flex; flex-wrap: wrap; gap: 18px; color: var(--tp-muted); font-size: 12px; }
.focus-stats b { margin-right: 3px; color: var(--tp-ink); font: 700 24px/1 var(--font-display); }
.session-card { color: var(--tp-training-ink); border-color: transparent; background: var(--tp-training-surface); }
.session-card .tp-kicker { color: var(--tp-secondary); }
.session-card h2,
.session-card .focus-stats b { color: var(--tp-training-ink); }
.session-card > p:not(.tp-kicker),
.session-card .focus-stats { color: #B9C0BB; }
.session-card::after { border-color: rgb(217 75 43 / 25%); }
.empty-mark { display: grid; width: 52px; height: 52px; place-items: center; border-radius: 20px 20px 20px 6px; color: var(--tp-ink); background: var(--tp-secondary); font: 800 20px/1 var(--font-display); transform: rotate(-3deg); }
.empty-focus small { color: var(--tp-muted); font-size: 11px; }

.saved-section { display: grid; gap: 12px; }
.section-title { display: flex; align-items: end; justify-content: space-between; }
.section-title h2 { margin: 5px 0 0; font-size: 24px; }
.plan-count { margin-left: 6px; color: var(--tp-muted); font-size: 15px; font-weight: 400; }
.new-plan { min-height: 44px; padding: 8px 14px; border: 1px solid var(--tp-line); border-radius: 24px; color: var(--tp-ink); background: var(--tp-surface); font-size: 13px; }
.library-note { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.section-title > b { color: var(--tp-muted); font: 700 34px/1 var(--font-display); }
.saved-list { display: grid; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 18px; background: var(--tp-surface); }
.saved-row { display: flex; align-items: stretch; border-top: 1px solid var(--tp-line); }
.saved-row:first-child { border-top: 0; }
.plan-open { display: flex; min-width: 0; min-height: 70px; flex: 1; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; border: 0; color: var(--tp-ink); background: transparent; text-align: left; }
.plan-open span { min-width: 0; }
.plan-open strong,
.plan-open small { display: block; }
.plan-open strong { overflow: hidden; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
.plan-open small { margin-top: 5px; color: var(--tp-muted); font-size: 11px; }
.plan-open > b { color: var(--tp-primary-readable); font-size: 12px; }
.plan-delete { min-width: 54px; border: 0; border-left: 1px solid var(--tp-line); color: var(--tp-danger); background: transparent; font-size: 11px; }
.show-all-plans { justify-self: start; min-height: 44px; padding: 0; border: 0; color: var(--tp-primary-readable); background: transparent; font-size: 12px; font-weight: 800; }
.saved-empty { display: flex; min-height: 88px; align-items: center; justify-content: space-between; gap: 14px; padding: 16px; border: 1px dashed #BDB9AC; border-radius: 18px; }
.saved-empty p { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.saved-empty a { min-height: 44px; color: var(--tp-primary-readable); font-size: 12px; font-weight: 800; white-space: nowrap; }
</style>
