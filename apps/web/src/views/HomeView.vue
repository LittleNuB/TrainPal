<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { analysisClient, browserEventStreamFactory } from '@/api/client'
import {
  localMediaAsFile,
  effectiveLocalUploadMaxBytes,
  probeVideoDuration,
  validateLocalMediaFile,
  validateLocalMediaUpload,
} from '@/domain/local-media'
import type { SourceSummary } from '@/domain/types'
import { useDialogFocus } from '@/composables/useDialogFocus'
import { useAnalysisStore } from '@/stores/analysis'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { useLocalMediaStore } from '@/stores/local-media'

const router = useRouter()
const analysis = useAnalysisStore()
const draft = useDraftStore()
const library = useLibraryStore()
const localMedia = useLocalMediaStore()
const initializing = ref(true)
const importing = ref(false)
const starting = ref(false)
const importError = ref('')
const quickRealSource = ref<SourceSummary | null>(null)
const quickRealDialog = ref<HTMLElement | null>(null)
const quickRealDialogFocus = useDialogFocus(quickRealDialog)

const current = computed(() => localMedia.current)
const previewUrl = computed(() => current.value ? localMedia.urlFor(current.value.sourceId) : null)
const canStart = computed(() => {
  if (!current.value || !analysis.capabilities || analysis.isRunning) return false
  return validateLocalMediaFile(
    localMediaAsFile(current.value),
    current.value.durationSeconds,
    analysis.capabilities,
  ).ok
})
const quickRealSources = computed(() => [...analysis.sources]
  .sort((left, right) => (
    left.duration_seconds - right.duration_seconds || left.id.localeCompare(right.id)
  ))
  .slice(0, 5))

const formatTime = (seconds: number): string => {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60).toString().padStart(2, '0')}`
}

const formatSize = (bytes: number): string => `${(bytes / 1_000_000).toFixed(1)} MB`

const formatDurationLimit = (seconds: number): string => {
  const whole = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(whole / 60)
  const remainder = whole % 60
  if (!minutes) return `${remainder} 秒`
  if (!remainder) return `${minutes} 分钟`
  return `${minutes} 分 ${remainder} 秒`
}

const formatRoundedDuration = (seconds: number): string => formatTime(Math.round(seconds))

const formatRoundedMinutes = (seconds: number): number => Math.max(1, Math.round(seconds / 60))

const capabilityCopy = computed(() => {
  const capability = analysis.capabilities
  if (analysis.capabilitiesLoading) return '正在读取当前分析能力…'
  if (!capability) return '暂时无法确认本地视频能力'
  if (!capability.local_upload_enabled) return '当前环境暂不支持本地视频分析'
  return `最长 ${formatDurationLimit(capability.local_analysis_max_seconds)} · 最大 ${formatSize(effectiveLocalUploadMaxBytes(capability))} · 结果需要核对`
})

const initialize = async (): Promise<void> => {
  try {
    await analysis.restore({ client: analysisClient, events: browserEventStreamFactory })
    if (analysis.currentSourceKind === 'local' && analysis.currentSourceId) {
      await localMedia.restore({ sourceId: analysis.currentSourceId })
    } else if (!localMedia.current) {
      await localMedia.restore()
    }
    await Promise.all([
      analysis.loadCapabilities(analysisClient),
      analysis.loadSources(analysisClient),
    ])
  } finally {
    initializing.value = false
  }
}

const chooseFile = async (event: Event): Promise<void> => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || importing.value) return
  importError.value = ''

  const capability = analysis.capabilities
  if (!capability) {
    importError.value = '还没有读取到分析能力，请稍后重试'
    return
  }
  const uploadValidation = validateLocalMediaUpload(file, capability)
  if (!uploadValidation.ok) {
    importError.value = uploadValidation.message
    return
  }

  importing.value = true
  try {
    const durationSeconds = await probeVideoDuration(file)
    const validation = validateLocalMediaFile(file, durationSeconds, capability)
    if (!validation.ok) {
      importError.value = validation.message
      return
    }
    if (analysis.isRunning) {
      const shouldReplace = window.confirm('选择新视频会取消当前分析，确定继续吗？')
      if (!shouldReplace) return
      await analysis.cancel(analysisClient).catch(() => undefined)
    } else if (analysis.hasOwnedRun) {
      analysis.clearResult()
    }
    await localMedia.importFile({ file, durationSeconds })
  } catch {
    importError.value = '无法读取这个视频，请换一个文件重试'
  } finally {
    importing.value = false
  }
}

const startLocalAnalysis = async (): Promise<void> => {
  if (!current.value || !canStart.value || starting.value) return
  starting.value = true
  importError.value = ''
  try {
    const request = analysis.start({
      sourceId: current.value.sourceId,
      file: localMediaAsFile(current.value),
      client: analysisClient,
      events: browserEventStreamFactory,
    })
    await router.push('/analysis')
    await request
  } finally {
    starting.value = false
  }
}

const startControlledAnalysis = async (sourceId: string): Promise<void> => {
  if (starting.value) return
  starting.value = true
  try {
    const request = analysis.start({
      sourceId,
      client: analysisClient,
      events: browserEventStreamFactory,
    })
    await router.push('/analysis')
    await request
  } finally {
    starting.value = false
  }
}

const openQuickRealAnalysis = async (source: SourceSummary, event: Event): Promise<void> => {
  quickRealSource.value = source
  await quickRealDialogFocus.activate(event.currentTarget as HTMLElement | null)
}

const closeQuickRealAnalysis = async (): Promise<void> => {
  quickRealSource.value = null
  await quickRealDialogFocus.deactivate()
}

const confirmQuickRealAnalysis = async (): Promise<void> => {
  const sourceId = quickRealSource.value?.id
  if (!sourceId || starting.value) return
  quickRealSource.value = null
  await quickRealDialogFocus.deactivate()
  await startControlledAnalysis(sourceId)
}

onMounted(initialize)
</script>

<template>
  <main class="tp-page home-page">
    <header class="home-hero">
      <div class="brand-row">TrainPal</div>
      <h1 class="tp-title">把想练的视频，<br>变成你的训练。</h1>
      <p class="tp-lead">选择视频，整理动作，再按自己的节奏练。</p>
    </header>

    <section class="import-workspace" aria-labelledby="import-title">
      <h2 id="import-title" class="tp-visually-hidden">从一个视频开始</h2>

      <div v-if="current && previewUrl" class="video-ticket tp-card">
        <video :src="previewUrl" controls preload="metadata" aria-label="已选择的视频预览" />
        <div class="ticket-copy">
          <div>
            <small>本机视频</small>
            <strong>{{ current.fileName }}</strong>
          </div>
          <dl>
            <div><dt>时长</dt><dd>{{ formatTime(current.durationSeconds) }}</dd></div>
            <div><dt>大小</dt><dd>{{ formatSize(current.sizeBytes) }}</dd></div>
          </dl>
        </div>
      </div>

      <label class="file-picker" :class="{ 'file-picker--secondary': current }">
        <input
          class="tp-visually-hidden"
          type="file"
          accept="video/mp4,video/quicktime,video/webm"
          :disabled="initializing || importing"
          @change="chooseFile"
        >
        <svg v-if="!current" class="upload-icon" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="12" y="8" width="40" height="48" rx="5" /><path d="M20 8v48M44 8v48M12 19h8m-8 13h8m-8 13h8m24-26h8m-8 13h8m-8 13h8M26 32l6-6 6 6m-6-6v17" /></svg>
        <span v-else aria-hidden="true">↻</span>
        <b>{{ importing ? '正在读取视频…' : current ? '更换视频' : '选择健身视频' }}</b>
        <small v-if="!current">选择你有权使用的视频</small>
      </label>

      <p class="capability-copy">{{ capabilityCopy }} · 原视频保存在当前设备；服务端临时副本只用于本次分析</p>
      <p v-if="importError" class="form-error" role="alert">{{ importError }}</p>
      <p v-if="analysis.isRunning" class="analysis-running-note" role="status">
        当前已有分析正在进行。请先查看进度，或在分析页明确取消后再开始新的分析。
      </p>

      <button
        v-if="current"
        class="tp-primary-action start-analysis"
        type="button"
        :disabled="!canStart || starting || importing"
        @click="startLocalAnalysis"
      >
        {{ starting ? '正在交给 TrainPal…' : '开始分析' }}
        <span aria-hidden="true">→</span>
      </button>

      <button
        v-if="analysis.capabilitiesError"
        class="tp-quiet-action retry-capability"
        type="button"
        @click="analysis.loadCapabilities(analysisClient)"
      >
        重新读取分析能力
      </button>
    </section>

    <details
      v-if="quickRealSources.length"
      class="quick-real-section"
    >
      <summary><span aria-hidden="true">▷</span>先用示例视频试试<span class="summary-chevron" aria-hidden="true">⌄</span></summary>
      <p>选择一段已授权视频开始 AI 分析，结果需要核对。</p>
      <div class="quick-real-sources" aria-label="按时长排序的真实分析来源">
        <button
          v-for="source in quickRealSources"
          :key="source.id"
          class="quick-real-source"
          type="button"
          :disabled="starting || analysis.isRunning"
          @click="openQuickRealAnalysis(source, $event)"
        >
          {{ formatRoundedDuration(source.duration_seconds) }}
        </button>
      </div>
    </details>

    <section v-if="draft.items.length || library.plans.length" class="recent-section" aria-labelledby="recent-title">
      <div class="section-line"><h2 id="recent-title">继续编辑上次的方案</h2></div>
      <div class="recent-links">
        <RouterLink v-if="draft.items.length" to="/plan">
          <span><small>当前方案</small><b>{{ draft.plan.name }}</b></span>
          <strong>{{ draft.items.length }} 个动作</strong>
        </RouterLink>
        <RouterLink v-if="library.plans.length" to="/train">
          <span><small>方案库</small><b>已保存的训练</b></span>
          <strong>{{ library.plans.length }} 份</strong>
        </RouterLink>
      </div>
    </section>

    <template v-if="quickRealSource">
      <button
        class="quick-real-backdrop"
        type="button"
        aria-label="取消快速真实分析"
        @click="closeQuickRealAnalysis"
      />
      <section
        ref="quickRealDialog"
        class="quick-real-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="quick-real-dialog-title"
        aria-describedby="quick-real-dialog-description"
        tabindex="-1"
        @keydown="quickRealDialogFocus.onKeydown($event, closeQuickRealAnalysis)"
      >
        <p class="tp-kicker">示例视频</p>
        <h2 id="quick-real-dialog-title">开始真实 AI 分析？</h2>
        <p id="quick-real-dialog-description">
          这段视频约 {{ formatRoundedMinutes(quickRealSource.duration_seconds) }} 分钟。分析会调用真实 AI，需要等待几分钟；结果需要核对，你可以编辑或删除不准确的动作。
        </p>
        <div class="quick-real-dialog-actions">
          <button
            class="tp-quiet-action quick-real-cancel"
            type="button"
            data-dialog-initial-focus
            @click="closeQuickRealAnalysis"
          >
            取消
          </button>
          <button
            class="tp-primary-action quick-real-confirm"
            type="button"
            :disabled="starting"
            @click="confirmQuickRealAnalysis"
          >
            {{ starting ? '正在开始…' : '确认并开始' }}
          </button>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.home-page { display: grid; max-width: 1000px; align-content: start; gap: 24px; }
.home-hero { display: grid; gap: 14px; padding-top: 4px; }
.brand-row { margin-bottom: 10px; font: 600 26px/1.3 var(--font-brand); }
.home-hero .tp-title { max-width: 650px; }
.home-hero .tp-lead { max-width: 590px; }

.import-workspace { display: grid; gap: 14px; }
.workspace-heading { display: flex; align-items: end; gap: 14px; padding-bottom: 14px; border-bottom: 1px solid var(--tp-line); }
.workspace-heading > span,
.section-line > span { color: var(--tp-primary); font: 700 28px/1 var(--font-display); }
.workspace-heading h2,
.section-line h2 { margin: 3px 0 0; color: var(--tp-ink); font: 700 28px/1 var(--font-display), var(--font-cn); }

.video-ticket { overflow: hidden; }
.video-ticket video { display: block; width: 100%; max-height: min(46dvh, 430px); aspect-ratio: 4 / 3; object-fit: contain; background: var(--tp-media-canvas); }
.ticket-copy { display: grid; gap: 13px; padding: 15px 16px; }
.ticket-copy > div { display: grid; gap: 3px; min-width: 0; }
.ticket-copy small { color: var(--tp-primary-readable); font: 700 11px/1 var(--font-display); letter-spacing: .1em; text-transform: uppercase; }
.ticket-copy strong { overflow: hidden; color: var(--tp-ink); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.ticket-copy dl { display: flex; margin: 0; gap: 20px; }
.ticket-copy dl div { display: flex; gap: 6px; }
.ticket-copy dt { color: var(--tp-muted); font-size: 12px; }
.ticket-copy dd { margin: 0; color: var(--tp-ink); font: 700 13px/1.4 var(--font-display); }

.file-picker { display: flex; min-height: 244px; flex-direction: column; align-items: center; justify-content: center; gap: 18px; padding: 28px 24px; border: 1px solid #CBD0BC; border-radius: 18px; color: var(--tp-secondary); background: var(--tp-sage-surface); cursor: pointer; }
.upload-icon { width: 68px; height: 68px; }
.file-picker b { display: grid; width: 100%; max-width: 340px; min-height: 48px; place-items: center; border-radius: 12px; color: var(--tp-surface); background: var(--tp-primary-readable); font-size: 15px; font-weight: 600; }
.file-picker small { color: var(--tp-muted); font-size: 12px; }
.file-picker:focus-within { outline: 2px solid var(--tp-focus); outline-offset: 3px; }
.file-picker > span { display: grid; width: 32px; height: 32px; place-items: center; border: 1px solid rgb(255 253 248 / 35%); border-radius: 50%; font-size: 22px; }
.file-picker--secondary { min-height: 50px; flex-direction: row; padding: 0 12px; border-color: var(--tp-line); color: var(--tp-ink); background: transparent; }
.file-picker--secondary b { width: auto; color: var(--tp-ink); background: transparent; }
.file-picker--secondary > span { border-color: var(--tp-line); font-size: 18px; }
.capability-copy { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.form-error { margin: 0; color: var(--tp-danger); font-size: 13px; line-height: 1.5; }
.analysis-running-note { margin: 0; color: var(--tp-primary-readable); font-size: 12px; line-height: 1.6; }
.start-analysis { width: 100%; min-height: 58px; margin-top: 4px; }
.start-analysis span { margin-left: auto; font-size: 20px; }
.retry-capability { justify-self: center; }

.quick-real-section { padding: 0 16px; border: 1px solid var(--tp-line); border-radius: 12px; background: var(--tp-surface); }
.quick-real-section summary { display: flex; min-height: 64px; align-items: center; gap: 12px; font-size: 14px; font-weight: 500; cursor: pointer; list-style: none; }
.quick-real-section summary::-webkit-details-marker { display: none; }
.summary-chevron { margin-left: auto; color: var(--tp-muted); }
.quick-real-section[open] { padding-bottom: 16px; }
.quick-real-section[open] .summary-chevron { transform: rotate(180deg); }
.quick-real-section .quick-real-sources { margin-top: 12px; }
.quick-real-section > p { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.quick-real-sources { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.quick-real-source { min-height: 48px; border: 1px solid var(--tp-line); border-radius: 14px; color: var(--tp-ink); background: var(--tp-surface); font: 700 16px/1 var(--font-display); }
.quick-real-source:focus-visible { outline: 2px solid var(--tp-focus); outline-offset: 2px; }
.quick-real-source:disabled { opacity: .55; }

.quick-real-backdrop { position: fixed; inset: 0; z-index: 70; width: 100%; border: 0; background: rgb(14 19 17 / 50%); backdrop-filter: blur(4px); }
.quick-real-dialog { position: fixed; right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); left: max(14px, env(safe-area-inset-left)); z-index: 71; display: grid; max-width: 520px; gap: 13px; margin: auto; padding: 22px; border-radius: 24px; color: var(--tp-ink); background: var(--tp-surface); box-shadow: var(--tp-shadow-float); }
.quick-real-dialog h2 { margin: 0; font-size: 25px; }
.quick-real-dialog > p:not(.tp-kicker) { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.65; }
.quick-real-dialog-actions { display: grid; grid-template-columns: minmax(0, .75fr) minmax(0, 1.25fr); gap: 9px; }
.quick-real-dialog-actions button { min-height: 48px; }

.recent-section { display: grid; gap: 14px; }
.section-line { display: flex; align-items: baseline; gap: 10px; }
.recent-links { display: grid; border-top: 1px solid var(--tp-line); }
.recent-links a { display: flex; min-height: 70px; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--tp-line); color: var(--tp-ink); text-decoration: none; }
.recent-links a span { display: grid; gap: 4px; }
.recent-links small { color: var(--tp-muted); font-size: 11px; }
.recent-links b { font-size: 14px; }
.recent-links strong { color: var(--tp-primary-readable); font: 700 14px/1 var(--font-display); }

@media (min-width: 768px) {
  .home-page { gap: 26px; }
  .ticket-copy { grid-template-columns: minmax(0, 1fr) auto; align-items: center; }
  .quick-real-sources { grid-template-columns: repeat(5, minmax(0, 1fr)); }
}

@media (min-width: 1024px) {
  .home-page { grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); column-gap: 48px; padding-top: 72px; }
  .home-hero { grid-column: 1; align-content: start; padding-top: 24px; }
  .brand-row { display: none; }
  .home-hero .tp-title { font-size: clamp(28px, 3vw, 38px); line-height: 1.5; }
  .import-workspace { grid-column: 2; grid-row: 1 / span 3; align-content: start; }
  .file-picker { min-height: 320px; }
  .file-picker--secondary { min-height: 50px; }
  .quick-real-section, .recent-section { grid-column: 1; }
  .section-line h2 { font-size: 16px; }
}

@media (prefers-reduced-motion: reduce) {
  .quick-real-source,
  .quick-real-dialog,
  .quick-real-backdrop { transition: none; animation: none; }
}
</style>
