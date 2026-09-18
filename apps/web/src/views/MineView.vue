<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { useDialogFocus } from '@/composables/useDialogFocus'
import { COACH_STYLE_LABELS } from '@/domain/coach'
import type { TrainingProfile } from '@/domain/training'
import CoachMotion from '@/features/experience/CoachMotion.vue'
import ProfileForm from '@/features/experience/ProfileForm.vue'
import { GYMTI_TYPE_PRESENTATION } from '@/features/gymti/presentation'
import { LocalDataCoordinationUnavailableError } from '@/local-data/clear-coordinator'
import { useLibraryStore } from '@/stores/library'
import { useGymtiStore } from '@/stores/gymti'
import { useLocalDataClearStore } from '@/stores/local-data-clear'

type DetailPanel = 'records' | 'gymti' | 'profile' | 'coach' | 'growth' | 'data' | null

const library = useLibraryStore()
const gymti = useGymtiStore()
const localDataClear = useLocalDataClearStore()
const pending = ref(false)
const notice = ref('')
const activePanel = ref<DetailPanel>(null)
const detailSheet = ref<HTMLElement | null>(null)
const dialogFocus = useDialogFocus(detailSheet)

const profileFields = computed(() => [
  library.profile.sex,
  library.profile.age,
  library.profile.heightCm,
  library.profile.weightKg,
].filter((value) => value !== null).length)

const gymtiLabel = computed(() => gymti.current
  ? GYMTI_TYPE_PRESENTATION[gymti.current.result.gymtiType].label
  : null)
const gymtiInProgress = computed(() => Boolean(gymti.attempt || gymti.pending))
const gymtiStatus = computed(() => gymtiInProgress.value
  ? '继续测评'
  : gymtiLabel.value ?? '开始测评')
const coachLabel = computed(() => library.preferences.coachStyleId
  ? COACH_STYLE_LABELS[library.preferences.coachStyleId]
  : '尚未确认')

const effectiveTrainingDays = computed(() => {
  const days = library.records
    .filter((record) => record.actions.some((action) => action.completedSets > 0))
    .map((record) => new Date(record.endedAt).toLocaleDateString('zh-CN'))
  return new Set(days).size
})

const formatDate = (value: string): string => new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
}).format(new Date(value))

const openPanel = async (
  panel: Exclude<DetailPanel, null>,
  event?: Event,
): Promise<void> => {
  activePanel.value = panel
  notice.value = ''
  await dialogFocus.activate(event?.currentTarget as HTMLElement | null)
}

const closePanel = async (): Promise<void> => {
  activePanel.value = null
  await dialogFocus.deactivate()
}

const saveProfile = async (
  profile: Omit<TrainingProfile, 'id' | 'updatedAt'>,
): Promise<void> => {
  try {
    await library.saveProfile(profile)
    notice.value = '训练档案已保存到本机'
  } catch {
    notice.value = '训练档案没有保存成功，请重试'
  }
}

const clearProfile = async (): Promise<void> => {
  try {
    await library.clearProfile()
    notice.value = '训练档案已清除'
  } catch {
    notice.value = '训练档案没有清除成功，请重试'
  }
}

const toggleCoach = async (): Promise<void> => {
  if (pending.value || library.persistenceSuspended) return
  pending.value = true
  try {
    await library.setPetVisible(!library.preferences.petVisible)
    notice.value = library.preferences.petVisible
      ? '训练中将显示 TrainPal 小猫教练'
      : '训练中已隐藏 TrainPal 小猫教练'
  } catch {
    notice.value = 'TrainPal 显示偏好没有保存成功，请重试'
  } finally {
    pending.value = false
  }
}

const clearEverything = async (): Promise<void> => {
  if (!window.confirm('将清除本机上的来源视频、分析恢复点、方案、训练进度与记录、GYMTI 答案与结果、训练档案和教练偏好。确定继续吗？')) return
  pending.value = true
  try {
    await localDataClear.clearAllLocalData()
    notice.value = '本机视频、问卷和训练数据已清除'
    closePanel()
  } catch (error) {
    notice.value = error instanceof LocalDataCoordinationUnavailableError
      ? '当前浏览器无法安全协调其他标签页，数据没有清除'
      : '本机训练数据没有清除成功，请重试'
  } finally {
    pending.value = false
  }
}

onMounted(async () => {
  try {
    await library.refreshHistory()
  } catch {
    notice.value = '训练记录暂时没有读取成功，请稍后重试'
  }
})
</script>

<template>
  <main class="mine-page tp-page">
    <header class="mine-hero">
      <div>
        <h1 class="tp-title">我的</h1>
        <p class="tp-lead">目标、教练和训练资料，只留在当前设备。</p>
      </div>
      <div v-if="library.preferences.coachStyleId" class="confirmed-coach">
        <CoachMotion :style-id="library.preferences.coachStyleId" state="idle" />
      </div>
      <div v-else class="coach-status" aria-label="尚未确认小猫教练风格">
        <strong>GYMTI</strong>
        <small>测评后确认小猫</small>
      </div>
    </header>

    <p v-if="notice && !activePanel" class="notice" role="status">{{ notice }}</p>

    <section class="growth-overview tp-card">
      <div>
        <p class="tp-kicker">陪伴成长</p>
        <h2><b>{{ effectiveTrainingDays }}</b> 个有效训练日</h2>
        <p>完整完成或留下实际完成量，都算一次与你并肩的训练日。</p>
      </div>
      <button type="button" aria-label="查看陪伴成长" @click="openPanel('growth', $event)">›</button>
    </section>

    <section class="settings-list" aria-label="我的 TrainPal 设置">
      <button type="button" class="setting-row" @click="openPanel('gymti', $event)">
        <span class="row-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="4" /><path d="m12 12 7-7" /></svg></span>
        <span><small>GYMTI 健身目标</small><strong>{{ gymtiStatus }}</strong></span>
        <b>›</b>
      </button>
      <button type="button" class="setting-row" @click="openPanel('coach', $event)">
        <span class="row-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 9V4l5 3h4l5-3v5a8 8 0 1 1-14 0Z" /><path d="M9 12h.01M15 12h.01m-4 4h2" /></svg></span>
        <span><small>小猫教练风格</small><strong>{{ coachLabel }}</strong></span>
        <b>›</b>
      </button>
      <button type="button" class="setting-row" @click="openPanel('profile', $event)">
        <span class="row-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4" /><path d="M5 21v-2a7 7 0 0 1 14 0v2" /></svg></span>
        <span><small>个人信息 · 可选</small><strong>{{ profileFields ? `已填写 ${profileFields}/4 项` : '尚未填写' }}</strong></span>
        <b>›</b>
      </button>
      <button type="button" class="setting-row" @click="openPanel('records', $event)">
        <span class="row-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 2v4m6-4v4M9 11h6m-6 4h4" /></svg></span>
        <span><small>训练记录</small><strong>{{ library.records.length ? `${library.records.length} 次训练` : '还没有记录' }}</strong></span>
        <b>›</b>
      </button>
      <button type="button" class="setting-row" @click="openPanel('data', $event)">
        <span class="row-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" /><path d="m8 12 3 3 5-6" /></svg></span>
        <span><small>设置与本机数据</small><strong>隐私、可见性与数据清理</strong></span>
        <b>›</b>
      </button>
    </section>

    <p class="local-only-note">数据保存在这台设备，不会跨设备同步。清除浏览器数据后无法恢复。</p>

    <template v-if="activePanel">
      <button class="sheet-backdrop" type="button" aria-label="关闭详情" @click="closePanel" />
      <section
        ref="detailSheet"
        class="detail-sheet"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="`panel-${activePanel}`"
        tabindex="-1"
        @keydown="dialogFocus.onKeydown($event, closePanel)"
      >
        <header>
          <div>
            <p class="tp-kicker">我的 TrainPal</p>
            <h2 :id="`panel-${activePanel}`">
              {{ activePanel === 'records' ? '训练记录' : activePanel === 'gymti' ? 'GYMTI 健身目标' : activePanel === 'profile' ? '个人信息' : activePanel === 'coach' ? '小猫教练风格' : activePanel === 'growth' ? '陪伴成长' : '设置与本机数据' }}
            </h2>
          </div>
          <button type="button" aria-label="关闭详情" data-dialog-initial-focus @click="closePanel">×</button>
        </header>

        <p v-if="notice" class="notice sheet-notice" role="status">{{ notice }}</p>

        <div v-if="activePanel === 'records'" class="record-panel">
          <div v-if="library.records.length" class="record-list">
            <RouterLink v-for="record in library.records" :key="record.id" :to="`/result/${record.id}`">
              <span>
                <strong>{{ record.plan.name }}</strong>
                <small>{{ formatDate(record.endedAt) }} · 约 {{ record.calorie.value }} 千卡</small>
              </span>
              <b>{{ record.outcome === 'completed' ? '已完成' : '提前结束' }}</b>
            </RouterLink>
          </div>
          <p v-else class="panel-empty">完成或提前结束一次训练后，实际完成量会保存在这里。</p>
        </div>

        <div v-else-if="activePanel === 'gymti'" class="placeholder-panel">
          <span class="big-letter">G</span>
          <h3>{{ gymtiInProgress ? '继续完成 GYMTI' : gymtiLabel ? `你的 GYMTI 是${gymtiLabel}` : '用 5–8 题了解训练取向' }}</h3>
          <p>GYMTI 会表达你的健身目标取向并采集训练经验，但不会根据身体信息推断人格或训练强度。</p>
          <RouterLink to="/personalize?from=/mine">{{ gymtiInProgress ? '继续测评' : gymtiLabel ? '查看测评结果' : '开始测评' }}</RouterLink>
        </div>

        <div v-else-if="activePanel === 'profile'" class="profile-panel">
          <p>年龄、性别、身高和体重主要用于卡路里约值，也只能作为个性策略的弱参考。全部字段都可跳过。</p>
          <ProfileForm :profile="library.profile" @save="saveProfile" @clear="clearProfile" />
        </div>

        <div v-else-if="activePanel === 'coach'" class="coach-panel">
          <h3>{{ library.preferences.coachStyleId ? `当前使用${coachLabel}` : '完成 GYMTI 后再确认风格' }}</h3>
          <p>问卷会给出一个推荐；在你明确确认前，正式页面不会展示任何小猫形象。教练风格不会改变动作与训练参数。</p>
          <RouterLink v-if="gymti.current" class="coach-edit-link" to="/personalize?from=/mine&view=styles">修改教练风格</RouterLink>
          <RouterLink v-else class="coach-edit-link" to="/personalize?from=/mine">去完成 GYMTI</RouterLink>
          <div class="preference-row">
            <span><strong>训练中显示 TrainPal</strong><small>隐藏不会移除训练控制与安全信息</small></span>
            <button type="button" :aria-pressed="library.preferences.petVisible" :disabled="pending || library.persistenceSuspended" @click="toggleCoach">
              {{ library.preferences.petVisible ? '已显示' : '已隐藏' }}
            </button>
          </div>
        </div>

        <div v-else-if="activePanel === 'growth'" class="growth-panel">
          <span>{{ effectiveTrainingDays }}</span>
          <h3>一起训练过的日子</h3>
          <p>每个认真练过的日子都算数。休息或中断不会让成长倒退，按自己的节奏继续就好。</p>
          <div class="milestone-track" aria-label="下一个里程碑为 3 个有效训练日">
            <i :style="{ width: `${Math.min(effectiveTrainingDays / 3, 1) * 100}%` }" />
          </div>
          <small>下一个里程碑：3 个有效训练日</small>
        </div>

        <div v-else class="data-panel">
          <section>
            <h3>本机保存</h3>
            <p>来源视频、方案、训练进度与记录、GYMTI 答案与结果、个人信息和教练偏好只保存在这台设备。</p>
          </section>
          <button type="button" class="clear-data" :disabled="pending" @click="clearEverything">清除本机训练数据</button>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.mine-page { display: grid; align-content: start; gap: 20px; }
.mine-hero { display: flex; align-items: end; justify-content: space-between; gap: 18px; padding: 14px 0 4px; }
.mine-hero .tp-title { margin-top: 7px; }
.mine-hero .tp-lead { margin-top: 10px; font-size: 13px; }
.confirmed-coach { display: grid; width: 100px; min-height: 108px; flex: 0 0 auto; place-items: center; }
.confirmed-coach :deep(.coach-motion__image) { width: 94px; filter: drop-shadow(0 8px 16px rgb(28 40 34 / 20%)); }
.coach-status { display: grid; width: 104px; min-height: 76px; flex: 0 0 auto; align-content: center; gap: 6px; padding: 12px; border: 1px dashed #B7B2A5; border-radius: 20px 20px 20px 7px; color: var(--tp-muted); background: rgb(255 253 248 / 55%); }
.coach-status strong { color: var(--tp-primary-readable); font: 700 14px/1 var(--font-display); letter-spacing: .08em; }
.coach-status small { font-size: 11px; font-weight: 700; line-height: 1.35; }
.notice { margin: 0; padding: 11px 13px; border-left: 3px solid var(--tp-secondary); border-radius: 0 10px 10px 0; color: var(--tp-success); background: rgb(165 186 99 / 12%); font-size: 12px; }

.growth-overview { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 20px; background: var(--tp-training-surface); }
.growth-overview .tp-kicker { color: var(--tp-secondary); }
.growth-overview h2 { margin: 7px 0 5px; color: var(--tp-training-ink); font-size: 20px; }
.growth-overview h2 b { margin-right: 5px; font: 700 38px/1 var(--font-display); }
.growth-overview p:not(.tp-kicker) { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.growth-overview button { width: 44px; min-width: 44px; min-height: 44px; border: 1px solid rgb(247 243 233 / 16%); border-radius: 50%; color: var(--tp-secondary); background: transparent; font-size: 30px; }

.settings-list { display: grid; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 20px; background: var(--tp-surface); box-shadow: var(--tp-shadow-soft); }
.setting-row { display: grid; grid-template-columns: 42px minmax(0, 1fr) 24px; min-height: 74px; align-items: center; gap: 12px; padding: 12px 14px; border: 0; border-top: 1px solid var(--tp-line); color: var(--tp-ink); background: transparent; text-align: left; }
.setting-row:first-child { border-top: 0; }
.row-icon { display: grid; width: 42px; height: 42px; place-items: center; border-radius: 12px; color: var(--tp-secondary); background: var(--tp-surface-raised); }
.row-icon svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.setting-row small,
.setting-row strong { display: block; }
.setting-row small { color: var(--tp-muted); font-size: 11px; }
.setting-row strong { margin-top: 5px; overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.setting-row > b { color: var(--tp-primary); font-size: 26px; font-weight: 400; }
.local-only-note { margin: 0; padding-inline: 4px; color: var(--tp-muted); font-size: 11px; line-height: 1.7; }

.sheet-backdrop { position: fixed; inset: 0; z-index: 60; width: 100%; border: 0; background: rgb(14 19 17 / 48%); backdrop-filter: blur(3px); }
.detail-sheet { position: fixed; right: 0; bottom: 0; left: 0; z-index: 61; display: grid; max-height: min(88dvh, 820px); gap: 18px; overflow-y: auto; padding: 20px clamp(16px, 4vw, 26px) calc(22px + env(safe-area-inset-bottom)); border-radius: 28px 28px 0 0; color: var(--tp-ink); background: var(--tp-surface); box-shadow: 0 -24px 70px rgb(14 19 17 / 24%); }
.detail-sheet > header { display: flex; align-items: center; justify-content: space-between; }
.detail-sheet h2 { margin: 5px 0 0; font-size: 26px; }
.detail-sheet > header button { width: 44px; min-height: 44px; border: 1px solid var(--tp-line); border-radius: 50%; color: var(--tp-ink); background: transparent; font-size: 25px; }
.record-list { display: grid; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 16px; }
.record-list a { display: flex; min-height: 66px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; border-top: 1px solid var(--tp-line); color: var(--tp-ink); text-decoration: none; }
.record-list a:first-child { border-top: 0; }
.record-list strong,
.record-list small { display: block; }
.record-list small { margin-top: 4px; color: var(--tp-muted); font-size: 11px; }
.record-list b { color: var(--tp-primary-readable); font-size: 11px; }
.panel-empty { margin: 0; padding: 30px 18px; border: 1px dashed #BDB9AC; border-radius: 16px; color: var(--tp-muted); font-size: 12px; line-height: 1.7; text-align: center; }
.placeholder-panel,
.coach-panel,
.growth-panel { display: grid; justify-items: start; gap: 10px; }
.big-letter,
.coach-placeholder { display: grid; width: 64px; height: 64px; place-items: center; border-radius: 24px 24px 24px 7px; color: var(--tp-ink); background: var(--tp-secondary); font: 800 26px/1 var(--font-display); transform: rotate(-3deg); }
.placeholder-panel h3,
.coach-panel h3,
.growth-panel h3 { margin: 4px 0 0; font-size: 22px; }
.placeholder-panel p,
.coach-panel p,
.growth-panel p,
.profile-panel > p,
.data-panel p { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.7; }
.placeholder-panel a { display: inline-flex; min-height: 44px; align-items: center; color: var(--tp-primary-readable); font-size: 12px; font-weight: 800; }
.coach-edit-link { display: inline-flex; min-height: 44px; align-items: center; color: var(--tp-primary-readable); font-size: 12px; font-weight: 800; }
.profile-panel { display: grid; gap: 18px; }
.profile-panel :deep(.profile-form) { padding-top: 4px; }
.profile-panel :deep(.profile-actions button:not(.quiet)) { color: var(--tp-surface); border-color: var(--tp-primary); background: var(--tp-primary-readable); }
.coach-placeholder { background: #F1C894; }
.preference-row { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 12px; margin-top: 8px; padding: 14px; border: 1px solid var(--tp-line); border-radius: 16px; }
.preference-row strong,
.preference-row small { display: block; }
.preference-row strong { font-size: 13px; }
.preference-row small { margin-top: 4px; color: var(--tp-muted); font-size: 11px; }
.preference-row button { min-width: 78px; min-height: 44px; border: 1px solid var(--tp-line); border-radius: 999px; color: var(--tp-primary-readable); background: #F7F3EA; font-weight: 800; }
.growth-panel > span { color: var(--tp-primary); font: 700 72px/.8 var(--font-display); }
.milestone-track { width: 100%; height: 8px; margin-top: 8px; overflow: hidden; border-radius: 999px; background: #E4E0D6; }
.milestone-track i { display: block; height: 100%; border-radius: inherit; background: var(--tp-secondary); }
.growth-panel > small { color: var(--tp-muted); font-size: 11px; }
.data-panel { display: grid; gap: 18px; }
.data-panel section { padding: 16px; border: 1px solid var(--tp-line); border-radius: 16px; background: #F7F3EA; }
.data-panel h3 { margin: 0 0 6px; font-size: 16px; }
.clear-data { min-height: 48px; border: 1px solid rgb(179 38 30 / 28%); border-radius: 999px; color: var(--tp-danger); background: transparent; font-weight: 800; }

@media (min-width: 760px) {
  .detail-sheet { top: 0; right: 0; bottom: 0; left: auto; width: min(520px, 100%); max-height: none; border-radius: 28px 0 0 28px; }
}

@media (min-width: 1024px) {
  .mine-page { grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); gap: 28px 48px; padding-top: 52px; }
  .mine-hero { grid-column: 1; align-items: center; }
  .growth-overview { grid-column: 1; align-self: start; }
  .settings-list { grid-column: 2; grid-row: 1 / span 3; align-self: start; }
  .setting-row { min-height: 94px; padding: 18px 22px; }
  .local-only-note { grid-column: 1; }
  .notice { grid-column: 1 / -1; }
}

@media (max-width: 359px) {
  .coach-status { width: 88px; }
  .preference-row { align-items: stretch; flex-direction: column; }
  .preference-row button { width: 100%; }
}
</style>
