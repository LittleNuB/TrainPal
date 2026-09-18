<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { analysisClient, browserEventStreamFactory } from '@/api/client'
import { useDialogFocus } from '@/composables/useDialogFocus'
import { localMediaAsFile } from '@/domain/local-media'
import type {
  CoverageGap,
  DraftPlan,
  LocalMediaFingerprint,
} from '@/domain/types'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import { useLocalMediaStore } from '@/stores/local-media'

type ProposalStrategy = 'append' | 'replace'

const router = useRouter()
const analysis = useAnalysisStore()
const draft = useDraftStore()
const localMedia = useLocalMediaStore()
const video = ref<HTMLVideoElement | null>(null)
const sourceId = ref('')
const sourceKind = ref<'controlled' | 'local' | null>(null)
const initializing = ref(true)
const mediaLoadFailed = ref(false)
const proposalWriting = ref(false)
const proposalError = ref('')
const sourceError = ref('')
const proposalDecisionOpen = ref(false)
const proposalDialog = ref<HTMLElement | null>(null)
const {
  activate: activateProposalDialog,
  deactivate: deactivateProposalDialog,
  onKeydown: onProposalDialogKeydown,
} = useDialogFocus(proposalDialog)

const syncSourceIdentity = (): void => {
  if (!analysis.currentSourceId || !analysis.currentSourceKind) return
  sourceId.value = analysis.currentSourceId
  sourceKind.value = analysis.currentSourceKind
}

const controlledSource = computed(() =>
  analysis.sources.find((source) => source.id === sourceId.value),
)
const localRecord = computed(() => (
  sourceKind.value === 'local' && localMedia.current?.sourceId === sourceId.value
    ? localMedia.current
    : null
))
const mediaUrl = computed(() => sourceKind.value === 'local'
  ? localMedia.urlFor(sourceId.value)
  : controlledSource.value?.media_url ?? null)
const sourceTitle = computed(() => sourceKind.value === 'local'
  ? localRecord.value?.fileName ?? '本地视频需要重新选择'
  : controlledSource.value?.title ?? '当前来源视频')
const sourceDuration = computed(() => (
  analysis.sourceDurationSeconds
  || localRecord.value?.durationSeconds
  || controlledSource.value?.duration_seconds
  || 0
))
const progressTotalSeconds = computed(() => analysis.gapRetrying
  ? analysis.gapRetrying.end_seconds - analysis.gapRetrying.start_seconds
  : sourceDuration.value)
const progressPercent = computed(() => {
  if (!progressTotalSeconds.value) return 0
  return Math.min(100, Math.round(
    (analysis.processedSeconds / progressTotalSeconds.value) * 100,
  ))
})
const pendingCandidates = computed(() => analysis.candidates.filter((candidate) => (
  candidate.needs_confirmation || candidate.parameters.mode === null
)))
const reliableCount = computed(() => analysis.candidates.length - pendingCandidates.value.length)
const hasProposal = computed(() => (
  analysis.status === 'completed'
  && analysis.coverageStatus !== 'insufficient'
  && analysis.candidates.length > 0
))
const hasSource = computed(() => Boolean(sourceId.value))
const canRetryWithCurrentSource = computed(() => (
  sourceKind.value === 'controlled' || Boolean(localRecord.value)
))

const formatTime = (seconds: number): string => {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60).toString().padStart(2, '0')}`
}

const initialize = async (): Promise<void> => {
  try {
    if (!analysis.currentSourceId) {
      await analysis.restore({ client: analysisClient, events: browserEventStreamFactory })
    }
    syncSourceIdentity()

    await analysis.loadSources(analysisClient)
    if (sourceKind.value === 'local' && sourceId.value) {
      await localMedia.restore({ sourceId: sourceId.value })
    }
  } finally {
    initializing.value = false
    await nextTick()
    const heading = document.querySelector<HTMLElement>('.analysis-page h1')
    if (heading) {
      heading.tabIndex = -1
      heading.focus({ preventScroll: true })
    }
  }
}

const retryRestore = async (): Promise<void> => {
  await analysis.restore({ client: analysisClient, events: browserEventStreamFactory })
  syncSourceIdentity()
  if (sourceKind.value === 'local' && sourceId.value) {
    await localMedia.restore({ sourceId: sourceId.value })
  }
}

onMounted(initialize)

const retryAnalysis = async (): Promise<void> => {
  if (!sourceId.value || analysis.isRunning) return
  sourceError.value = ''
  proposalError.value = ''
  const file = sourceKind.value === 'local' && localRecord.value
    ? localMediaAsFile(localRecord.value)
    : undefined
  if (sourceKind.value === 'local' && !file) {
    sourceError.value = '原视频已不在本机，请返回首页重新选择后再分析。'
    return
  }
  video.value?.pause()
  await analysis.start({
    sourceId: sourceId.value,
    file,
    client: analysisClient,
    events: browserEventStreamFactory,
  })
}

const cancelAnalysis = async (): Promise<void> => {
  try {
    await analysis.cancel(analysisClient)
  } catch {
    // The store has already applied the local cancellation state.
  }
}

const retryGap = async (gap: CoverageGap): Promise<void> => {
  if (!localRecord.value) {
    sourceError.value = '原视频已不在本机，请返回首页重新选择后再重试这段。'
    return
  }
  sourceError.value = ''
  video.value?.pause()
  await analysis.retryGap({
    gap,
    file: localMediaAsFile(localRecord.value),
    client: analysisClient,
    events: browserEventStreamFactory,
  })
}

const sourceSnapshots = (): Record<string, {
  title: string
  origin_url: string | null
  kind?: 'controlled' | 'local'
  localMedia?: LocalMediaFingerprint
}> => {
  const snapshots: Record<string, {
    title: string
    origin_url: string | null
    kind?: 'controlled' | 'local'
    localMedia?: LocalMediaFingerprint
  }> = Object.fromEntries(
    analysis.sources.map((source) => [source.id, {
      title: source.title,
      origin_url: source.origin_url ?? null,
      kind: 'controlled' as const,
    }]),
  )
  if (localRecord.value) {
    const record = localRecord.value
    snapshots[record.sourceId] = {
      title: record.fileName,
      origin_url: null,
      kind: 'local',
      localMedia: {
        fileName: record.fileName,
        mimeType: record.mimeType,
        sizeBytes: record.sizeBytes,
        lastModified: record.lastModified,
        durationSeconds: record.durationSeconds,
      },
    }
  }
  return snapshots
}

const applyProposal = async (strategy: ProposalStrategy): Promise<void> => {
  if (!hasProposal.value || proposalWriting.value) return
  proposalWriting.value = true
  proposalError.value = ''
  let previousPlan: DraftPlan | null = null
  let proposalApplied = false
  try {
    await draft.flushPersist()
    previousPlan = JSON.parse(JSON.stringify(draft.plan)) as DraftPlan
    draft.applyCandidateProposal(analysis.candidates, sourceSnapshots(), strategy)
    proposalApplied = true
    await draft.flushPersist()
    proposalDecisionOpen.value = false
    await router.push('/plan')
  } catch {
    if (proposalApplied && previousPlan) {
      try {
        await draft.reload()
      } catch {
        draft.adoptPersistedPlan(previousPlan)
      }
    }
    proposalError.value = '训练方案没有保存成功，原方案保持不变。请重试。'
  } finally {
    proposalWriting.value = false
  }
}

const openPreparedPlan = async (event: Event): Promise<void> => {
  if (!draft.items.length) {
    await applyProposal('replace')
    return
  }
  proposalDecisionOpen.value = true
  await activateProposalDialog(event.currentTarget as HTMLElement)
}

const closeProposalDecision = async (): Promise<void> => {
  if (proposalWriting.value) return
  proposalDecisionOpen.value = false
  await deactivateProposalDialog()
}

const handleProposalDialogKeydown = (event: KeyboardEvent): void => {
  onProposalDialogKeydown(event, () => { void closeProposalDecision() })
}
</script>

<template>
  <main class="analysis-page tp-page tp-page--immersive">
    <header class="analysis-header">
      <RouterLink to="/" class="back-link" aria-label="返回首页">←</RouterLink>
      <span>视频分析</span>
      <RouterLink to="/plan" class="plan-link" aria-label="查看当前训练方案">
        方案 {{ draft.items.length || '' }}
      </RouterLink>
    </header>

    <section v-if="initializing" class="state-card tp-card" aria-live="polite">
      <span class="status-dot status-dot--active" aria-hidden="true" />
      <div><strong>正在找回这次分析</strong><p>只读取真实任务状态，不会重新创建请求。</p></div>
    </section>

    <template v-else-if="hasSource">
      <section class="source-preview tp-card" aria-labelledby="source-title">
        <div class="source-copy">
          <p class="tp-kicker">当前视频</p>
          <h1 id="source-title">{{ sourceTitle }}</h1>
          <p>{{ sourceKind === 'local' ? '本机视频' : '受控示例' }} · {{ formatTime(sourceDuration) }}</p>
        </div>
        <video
          v-if="mediaUrl"
          ref="video"
          :src="mediaUrl"
          controls
          playsinline
          preload="metadata"
          aria-label="当前分析来源预览"
          @error="mediaLoadFailed = true"
        />
        <div v-else class="media-placeholder">
          <span aria-hidden="true">▶</span>
          <p>预览暂不可用，分析任务和方案结果仍会保留。</p>
        </div>
        <p v-if="mediaLoadFailed" class="inline-warning" role="status">来源预览暂不可用，不影响查看已完成的分析。</p>
      </section>

      <section v-if="analysis.restoring || analysis.restoreError" class="state-card tp-card" aria-live="polite">
        <span class="status-dot status-dot--active" aria-hidden="true" />
        <div>
          <strong>{{ analysis.restoring ? '正在恢复真实进度' : analysis.restoreError }}</strong>
          <p>{{ analysis.restoring ? '页面离开不会取消这次任务。' : '任务仍保留在服务端短时恢复窗口内。' }}</p>
        </div>
        <button v-if="analysis.restoreError" type="button" @click="retryRestore">重试恢复</button>
      </section>

      <section v-else-if="analysis.isRunning" class="progress-card tp-card" aria-live="polite">
        <div class="progress-heading">
          <div>
            <p class="tp-kicker">正在整理动作</p>
            <h2>{{ analysis.stageLabel }}</h2>
          </div>
          <b>{{ formatTime(analysis.processedSeconds) }} / {{ formatTime(progressTotalSeconds) }}</b>
        </div>
        <div
          class="progress-track"
          role="progressbar"
          aria-label="视频覆盖进度"
          :aria-valuenow="progressPercent"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <i :style="{ width: `${progressPercent}%` }" />
        </div>
        <div class="discovery-count">
          <strong>{{ analysis.discoveredCandidateCount }}</strong>
          <span>个动作线索</span>
          <small>{{ analysis.gapRetrying ? `正在重试 ${formatTime(analysis.gapRetrying.start_seconds)}—${formatTime(analysis.gapRetrying.end_seconds)}` : '可以离开，任务会继续' }}</small>
        </div>
        <button type="button" class="cancel-action" @click="cancelAnalysis">取消分析</button>
      </section>

      <section v-else-if="hasProposal" class="completion-card tp-card" aria-labelledby="completion-title">
        <div class="completion-mark" aria-hidden="true">✓</div>
        <div class="completion-copy">
          <p class="tp-kicker">分析结果</p>
          <h2 id="completion-title">训练方案已准备好</h2>
          <p>
            找到 {{ analysis.candidates.length }} 个动作。
            <template v-if="reliableCount">{{ reliableCount }} 个可以直接训练</template><template v-else>都需要你先确认</template><template v-if="pendingCandidates.length">，{{ pendingCandidates.length }} 个会在方案中标记为待确认</template>。
          </p>
        </div>

        <div class="completion-metrics" aria-label="分析完成摘要">
          <div><b>{{ analysis.candidates.length }}</b><span>动作</span></div>
          <div><b>{{ reliableCount }}</b><span>可执行</span></div>
          <div><b>{{ analysis.coverageGaps.length }}</b><span>覆盖缺口</span></div>
        </div>

        <div v-if="analysis.warnings.length" class="analysis-warnings" role="status">
          <p v-for="warning in analysis.warnings" :key="warning.code">{{ warning.message }}</p>
        </div>

        <div v-if="analysis.coverageStatus === 'insufficient'" class="analysis-warnings" role="status">
          <p>这次可靠覆盖不足，现有结果不会被当作完整方案。可以重试未覆盖片段，或返回首页更换视频。</p>
        </div>

        <section v-if="analysis.coverageGaps.length" class="coverage-gaps" aria-labelledby="coverage-title">
          <div>
            <strong id="coverage-title">还有 {{ analysis.coverageGaps.length }} 段未可靠覆盖</strong>
            <p>已识别动作仍可进入方案；缺口不会伪装成动作。</p>
          </div>
          <article v-for="gap in analysis.coverageGaps" :key="`${gap.start_seconds}-${gap.end_seconds}`">
            <span>{{ formatTime(gap.start_seconds) }}—{{ formatTime(gap.end_seconds) }}</span>
            <button
              type="button"
              :disabled="!gap.retryable || Boolean(analysis.gapRetrying)"
              @click="retryGap(gap)"
            >
              {{ analysis.gapRetrying === gap ? '正在重试…' : '重试这段' }}
            </button>
          </article>
          <p v-if="analysis.gapRetryError" class="inline-error" role="alert">{{ analysis.gapRetryError }}</p>
        </section>

        <p v-if="proposalError" class="inline-error" role="alert">{{ proposalError }}</p>
        <button
          type="button"
          class="tp-primary-action prepared-plan-action"
          :disabled="proposalWriting || Boolean(analysis.gapRetrying)"
          @click="openPreparedPlan"
        >
          {{ proposalWriting ? '正在保存方案…' : '查看训练方案' }}<span aria-hidden="true">→</span>
        </button>
      </section>

      <section v-else-if="analysis.status === 'completed'" class="state-card state-card--stacked tp-card">
        <span class="status-symbol" aria-hidden="true">?</span>
        <div>
          <strong>{{ analysis.coverageStatus === 'insufficient' ? '可靠覆盖还不够生成方案' : '没有足够可靠的动作证据' }}</strong>
          <p v-if="analysis.coverageStatus === 'insufficient'">
            已识别的线索不会被当作完整方案。请先重试未覆盖片段，或手工创建动作。
          </p>
          <p v-else>这不是系统失败。你可以重新分析，或在方案中手工创建动作。</p>
        </div>
        <section
          v-if="analysis.coverageStatus === 'insufficient' && analysis.coverageGaps.length"
          class="coverage-gaps"
          aria-labelledby="insufficient-coverage-title"
        >
          <div>
            <strong id="insufficient-coverage-title">还有 {{ analysis.coverageGaps.length }} 段未可靠覆盖</strong>
            <p>补齐可靠证据后，TrainPal 才会生成基础方案。</p>
          </div>
          <article v-for="gap in analysis.coverageGaps" :key="`${gap.start_seconds}-${gap.end_seconds}`">
            <span>{{ formatTime(gap.start_seconds) }}—{{ formatTime(gap.end_seconds) }}</span>
            <button
              type="button"
              :disabled="!gap.retryable || Boolean(analysis.gapRetrying)"
              @click="retryGap(gap)"
            >
              {{ analysis.gapRetrying === gap ? '正在重试…' : '重试这段' }}
            </button>
          </article>
          <p v-if="analysis.gapRetryError" class="inline-error" role="alert">{{ analysis.gapRetryError }}</p>
        </section>
        <div class="state-actions">
          <button type="button" :disabled="!canRetryWithCurrentSource" @click="retryAnalysis">重新分析</button>
          <RouterLink to="/plan">手工创建动作</RouterLink>
        </div>
      </section>

      <section v-else-if="analysis.status === 'failed' && analysis.failureKind === 'capacity'" class="state-card state-card--stacked tp-card" role="alert">
        <span class="status-symbol" aria-hidden="true">…</span>
        <div>
          <strong>实时 AI 名额正在使用</strong>
          <p>{{ analysis.retryAfterSeconds ? `约 ${analysis.retryAfterSeconds} 秒后可重试` : '请稍后重试；不会用假结果代替。' }}</p>
        </div>
        <div class="state-actions">
          <button type="button" :disabled="!canRetryWithCurrentSource" @click="retryAnalysis">重试</button>
          <RouterLink to="/train">使用快速体验方案</RouterLink>
        </div>
      </section>

      <section v-else-if="analysis.status === 'failed'" class="state-card state-card--stacked tp-card" role="alert">
        <span class="status-symbol status-symbol--error" aria-hidden="true">!</span>
        <div>
          <strong>这次没有分析成功</strong>
          <p>{{ analysis.error?.message ?? analysis.restoreError ?? '系统没有返回可用结果。' }}</p>
        </div>
        <button type="button" :disabled="!canRetryWithCurrentSource" @click="retryAnalysis">重试分析</button>
      </section>

      <section v-else-if="analysis.status === 'cancelled'" class="state-card state-card--stacked tp-card">
        <span class="status-symbol" aria-hidden="true">×</span>
        <div><strong>已取消分析</strong><p>来源仍在当前设备，可以重新开始。</p></div>
        <button type="button" :disabled="!canRetryWithCurrentSource" @click="retryAnalysis">重新分析</button>
      </section>

      <section v-else class="state-card state-card--stacked tp-card">
        <span class="status-symbol" aria-hidden="true">↗</span>
        <div><strong>没有进行中的分析</strong><p>请先选择健身视频，再开始分析。</p></div>
        <RouterLink to="/">返回首页</RouterLink>
      </section>

      <p v-if="sourceError" class="source-error" role="alert">{{ sourceError }}</p>
    </template>

    <section v-else class="state-card state-card--stacked tp-card">
      <span class="status-symbol" aria-hidden="true">↗</span>
      <div><h1>没有找到分析来源</h1><p>请先选择健身视频，再开始分析。</p></div>
      <RouterLink to="/">返回首页</RouterLink>
    </section>

    <template v-if="proposalDecisionOpen">
      <button class="dialog-backdrop" type="button" aria-label="取消写入方案" @click="closeProposalDecision" />
      <section
        ref="proposalDialog"
        class="proposal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="proposal-dialog-title"
        tabindex="-1"
        @keydown="handleProposalDialogKeydown"
      >
        <p class="tp-kicker">选择方案</p>
        <h2 id="proposal-dialog-title">当前已经有训练方案</h2>
        <p>这次找到的动作，要加入当前方案，还是替换它？</p>
        <p v-if="proposalError" class="inline-error" role="alert">{{ proposalError }}</p>
        <div class="proposal-options">
          <button
            type="button"
            class="append-option"
            data-dialog-initial-focus
            :disabled="proposalWriting"
            @click="applyProposal('append')"
          >
            <strong>追加</strong><span>保留当前 {{ draft.items.length }} 个动作，把新动作放在后面</span>
          </button>
          <button type="button" :disabled="proposalWriting" @click="applyProposal('replace')">
            <strong>替换</strong><span>用这次分析结果建立新的当前方案</span>
          </button>
        </div>
        <button type="button" class="cancel-option" :disabled="proposalWriting" @click="closeProposalDecision">取消</button>
      </section>
    </template>
  </main>
</template>

<style scoped>
.analysis-page { display: grid; align-content: start; max-width: 1120px; gap: 20px; }
.analysis-header { display: grid; grid-template-columns: minmax(44px, auto) 1fr minmax(44px, auto); align-items: center; }
.analysis-header > span { color: var(--tp-muted); font-size: 13px; font-weight: 800; text-align: center; }
.back-link { display: grid; width: 44px; height: 44px; place-items: center; border: 1px solid var(--tp-line); border-radius: 50%; color: var(--tp-ink); background: var(--tp-surface); font-size: 22px; text-decoration: none; }
.plan-link { display: grid; min-height: 44px; place-items: center; padding: 0 12px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: var(--tp-surface); font-size: 12px; font-weight: 800; text-decoration: none; }

.source-preview { display: grid; overflow: hidden; }
.source-copy { display: grid; gap: 5px; padding: 17px 18px; }
.source-copy h1 { overflow: hidden; margin: 0; color: var(--tp-ink); font-size: clamp(20px, 6vw, 30px); text-overflow: ellipsis; white-space: nowrap; }
.source-copy > p:last-child { margin: 0; color: var(--tp-muted); font-size: 12px; }
.source-preview video { display: block; width: 100%; max-height: min(42dvh, 420px); aspect-ratio: 16 / 9; object-fit: contain; background: var(--tp-media-canvas); }
.media-placeholder { display: grid; min-height: 180px; place-items: center; padding: 24px; color: var(--tp-muted); background: var(--tp-sage-surface); text-align: center; }
.media-placeholder span { font-size: 32px; }
.media-placeholder p { margin: 8px 0 0; font-size: 12px; }
.inline-warning { margin: 0; padding: 10px 16px; color: #72501B; background: #FFF4DC; font-size: 12px; }

.state-card { display: flex; align-items: center; gap: 13px; padding: 18px; }
.state-card > div:not(.state-actions) { flex: 1; }
.state-card strong { color: var(--tp-ink); font-size: 16px; }
.state-card h1 { margin: 0; color: var(--tp-ink); font-size: 16px; }
.state-card p { margin: 5px 0 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.state-card button,
.state-card > a,
.state-actions a { display: grid; min-height: 44px; place-items: center; padding: 0 15px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: transparent; font-size: 12px; font-weight: 800; text-decoration: none; }
.state-card--stacked { display: grid; justify-items: start; }
.status-dot { width: 10px; height: 10px; flex: 0 0 auto; border-radius: 50%; background: var(--tp-muted); }
.status-dot--active { background: var(--tp-primary); box-shadow: 0 0 0 5px rgb(217 75 43 / 12%); animation: pulse 1.5s ease-in-out infinite; }
.status-symbol { display: grid; width: 42px; height: 42px; place-items: center; border-radius: 50%; color: #FFFEFA; background: var(--tp-secondary); font: 800 20px/1 var(--font-display); }
.status-symbol--error { color: var(--tp-surface); background: var(--tp-danger); }
.state-actions { display: flex; flex-wrap: wrap; gap: 8px; }

.progress-card { display: grid; gap: 18px; padding: 22px; color: var(--tp-training-ink); background: var(--tp-training-surface); }
.progress-heading { display: flex; align-items: end; justify-content: space-between; gap: 12px; }
.progress-heading h2 { margin: 6px 0 0; color: var(--tp-training-ink); font-size: 23px; }
.progress-heading b { color: var(--tp-secondary); font: 700 15px/1 var(--font-display); white-space: nowrap; }
.progress-track { height: 7px; overflow: hidden; border-radius: 12px; background: var(--tp-line); }
.progress-track i { display: block; height: 100%; border-radius: inherit; background: var(--tp-secondary); transition: width .25s ease; }
.discovery-count { display: grid; grid-template-columns: auto 1fr; align-items: baseline; gap: 0 8px; }
.discovery-count strong { color: var(--tp-training-ink); font: 700 42px/1 var(--font-display); }
.discovery-count span { color: var(--tp-training-ink); font-size: 13px; font-weight: 800; }
.discovery-count small { grid-column: 1 / -1; margin-top: 5px; color: var(--tp-muted); font-size: 11px; }
.cancel-action { justify-self: start; min-height: 44px; padding: 0 15px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-training-ink); background: transparent; }

.completion-card { display: grid; grid-template-columns: auto 1fr; gap: 14px; padding: 21px; }
.completion-mark { display: grid; width: 48px; height: 48px; place-items: center; border-radius: 50%; color: var(--tp-surface); background: var(--tp-primary); font-size: 23px; font-weight: 900; }
.completion-copy h2 { margin: 6px 0 5px; color: var(--tp-ink); font-size: clamp(24px, 7vw, 34px); }
.completion-copy > p:last-child { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.65; }
.completion-metrics { display: grid; grid-column: 1 / -1; grid-template-columns: repeat(3, 1fr); border-block: 1px solid var(--tp-line); }
.completion-metrics div { display: grid; gap: 4px; min-width: 0; padding: 15px 8px; text-align: center; }
.completion-metrics div + div { border-left: 1px solid var(--tp-line); }
.completion-metrics b { color: var(--tp-ink); font: 700 27px/1 var(--font-display); }
.completion-metrics span { color: var(--tp-muted); font-size: 11px; }
.analysis-warnings { grid-column: 1 / -1; padding: 11px 13px; border-left: 3px solid #B48A49; color: #72501B; background: #FFF7E8; }
.analysis-warnings p { margin: 0; font-size: 12px; line-height: 1.5; }
.coverage-gaps { display: grid; grid-column: 1 / -1; gap: 10px; padding: 15px; border: 1px solid #DDBD85; border-radius: 15px; background: #FFF9ED; }
.coverage-gaps > div strong { color: #72501B; font-size: 13px; }
.coverage-gaps > div p { margin: 4px 0 0; color: #7C6B4E; font-size: 11px; line-height: 1.5; }
.coverage-gaps article { display: flex; min-height: 44px; align-items: center; justify-content: space-between; gap: 10px; border-top: 1px solid #E9D8B9; }
.coverage-gaps article span { color: var(--tp-ink); font: 700 13px/1 var(--font-display); }
.coverage-gaps article button { min-height: 44px; padding: 0; border: 0; color: var(--tp-primary-readable); background: transparent; font-size: 12px; font-weight: 800; }
.prepared-plan-action { grid-column: 1 / -1; width: 100%; min-height: 56px; }
.prepared-plan-action span { margin-left: auto; font-size: 20px; }
.inline-error,
.source-error { margin: 0; color: var(--tp-danger); font-size: 12px; line-height: 1.5; }
.completion-card > .inline-error { grid-column: 1 / -1; }
.source-error { padding: 12px 14px; border: 1px solid rgb(179 38 30 / 25%); border-radius: 12px; background: rgb(179 38 30 / 5%); }

.dialog-backdrop { position: fixed; inset: 0; z-index: 70; width: 100%; border: 0; background: rgb(14 19 17 / 50%); backdrop-filter: blur(4px); }
.proposal-dialog { position: fixed; right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); left: max(14px, env(safe-area-inset-left)); z-index: 71; display: grid; max-width: 520px; gap: 13px; margin: auto; padding: 22px; border-radius: 24px; color: var(--tp-ink); background: var(--tp-surface); box-shadow: var(--tp-shadow-float); }
.proposal-dialog h2 { margin: 0; font-size: 25px; }
.proposal-dialog > p:not(.tp-kicker, .inline-error) { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.proposal-options { display: grid; gap: 8px; }
.proposal-options button { display: grid; min-height: 70px; gap: 4px; padding: 13px 15px; border: 1px solid var(--tp-line); border-radius: 15px; color: var(--tp-ink); background: transparent; text-align: left; }
.proposal-options .append-option { border-color: var(--tp-primary); background: rgb(217 75 43 / 5%); }
.proposal-options strong { font-size: 14px; }
.proposal-options span { color: var(--tp-muted); font-size: 11px; line-height: 1.5; }
.cancel-option { min-height: 44px; border: 0; color: var(--tp-muted); background: transparent; font-weight: 800; }

@keyframes pulse { 50% { opacity: .45; transform: scale(.85); } }

@media (min-width: 768px) {
  .source-preview { grid-template-columns: minmax(0, 1fr) minmax(320px, 1.25fr); align-items: center; }
  .source-preview video,
  .media-placeholder { grid-column: 2; grid-row: 1; min-height: 250px; }
  .source-copy { padding: 28px; }
  .completion-card { padding: 28px; }
}

@media (prefers-reduced-motion: reduce) {
  .status-dot--active { animation: none; }
  .progress-track i { transition: none; }
}
@media (min-width: 1024px) {
  .analysis-page { grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); align-items: start; gap: 24px 32px; }
  .analysis-header { grid-column: 1 / -1; }
  .source-preview { grid-column: 1; display: flex; flex-direction: column; }
  .source-copy { width: 100%; padding: 22px; }
  .source-preview video { max-height: 420px; }
  .state-card, .progress-card, .completion-card { grid-column: 2; }
  .source-error { grid-column: 1 / -1; }
}
</style>
