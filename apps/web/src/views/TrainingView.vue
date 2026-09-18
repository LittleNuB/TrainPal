<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { analysisClient } from '@/api/client'
import type { CoachMotionCue, CoachMotionEvent } from '@/domain/coach'
import { fingerprintMatches, probeVideoDuration, SUPPORTED_LOCAL_MEDIA_TYPES } from '@/domain/local-media'
import { toSafeOriginUrl } from '@/domain/source'
import CoachMotion from '@/features/experience/CoachMotion.vue'
import SourceTips from '@/features/experience/SourceTips.vue'
import { currentPlaybackRange } from '@/domain/playback'
import { derivePetState } from '@/features/experience/pet-state'
import { useAnalysisStore } from '@/stores/analysis'
import { useLibraryStore } from '@/stores/library'
import { useLocalMediaStore } from '@/stores/local-media'
import { useTrainingStore } from '@/stores/training'
import type { TrainingEngineResult } from '@/training/training-engine'

const analysis = useAnalysisStore()
const library = useLibraryStore()
const localMedia = useLocalMediaStore()
const training = useTrainingStore()
const video = ref<HTMLVideoElement | null>(null)
const nowMilliseconds = ref(Date.now())
const commandPending = ref(false)
const mediaLoadFailed = ref(false)
const localMediaUrl = ref<string | null>(null)
const localMediaResolving = ref(false)
const localMediaError = ref('')
const screenReaderAnnouncement = ref('')
const coachCue = ref<CoachMotionCue | null>(null)
const soundEnabled = ref(true)
let cueAudio: AudioContext | null = null
let unduckTimer: ReturnType<typeof setTimeout> | null = null
let originalVolume: number | null = null
let ticker: ReturnType<typeof setInterval> | null = null
let leavePause: Promise<void> | null = null
let cueSequence = 0
let countdownRestKey: string | null = null

const session = computed(() => training.session)
const item = computed(() => training.currentItem)
const progress = computed(() => training.currentProgress)
const source = computed(() => {
  const sourceId = item.value?.sourceRef?.sourceId
  return sourceId ? analysis.sources.find((entry) => entry.id === sourceId) ?? null : null
})
const isLocalSource = computed(() => Boolean(
  item.value?.sourceRef
  && (
    item.value.sourceRef.kind === 'local'
    || item.value.sourceRef.sourceId.startsWith('local:')
  ),
))
const mediaUrl = computed(() => isLocalSource.value ? localMediaUrl.value : source.value?.media_url ?? null)
const segment = computed(() => item.value ? currentPlaybackRange(item.value) : null)
const hasPlayableVideo = computed(() => Boolean(
  item.value?.sourceRef && mediaUrl.value && segment.value && !mediaLoadFailed.value,
))
const mediaBadge = computed(() => {
  if (!item.value?.sourceRef) return '自建动作'
  if (!hasPlayableVideo.value) return isLocalSource.value ? '本地视频不可用' : '参考视频不可用'
  return isLocalSource.value ? '本地参考片段' : '参考视频片段'
})
const originalUrl = computed(() => toSafeOriginUrl(item.value?.sourceRef?.originUrl))
const targetSets = computed(() => item.value?.sets.value ?? 0)
const actionPosition = computed(() => {
  if (!session.value) return ''
  return `动作 ${session.value.currentItemIndex + 1} / ${session.value.plan.items.length}`
})
const setNumber = computed(() => Math.min(
  (session.value?.currentSetIndex ?? 0) + 1,
  targetSets.value,
))
const restRemainingSeconds = computed(() => {
  if (session.value?.status !== 'resting' || !session.value.restEndsAt) return 0
  return Math.max(0, Math.ceil(
    (Date.parse(session.value.restEndsAt) - nowMilliseconds.value) / 1_000,
  ))
})
const countdownSeconds = computed(() => session.value?.status === 'countdown'
  ? Math.max(0, Math.ceil((Date.parse(session.value.countdownEndsAt ?? '') - nowMilliseconds.value) / 1_000)) : 0)
const durationRemainingSeconds = computed(() => {
  if (item.value?.mode !== 'duration') return 0
  const target = item.value.durationSeconds.value ?? 0
  const elapsed = (session.value?.currentSetActiveMilliseconds ?? 0) / 1_000
  return Math.max(0, Math.ceil(target - elapsed))
})
const statusLabel = computed(() => {
  if (!session.value) return '没有未完成训练'
  if (session.value.status === 'countdown') return '准备开始'
  if (session.value.status === 'active') return item.value?.mode === 'duration' ? '倒计时进行中' : '本组进行中'
  if (session.value.status === 'resting') return '组间休息'
  if (session.value.status === 'ready_to_continue') return '准备继续'
  if (session.value.pauseReason === 'before_start') return '准备开始'
  if (session.value.pauseReason === 'between_actions') return '下一个动作'
  if (session.value.pauseReason === 'recovered') return '已恢复并暂停'
  return '训练已暂停'
})
const startActionLabel = computed(() => {
  if (session.value?.status === 'ready_to_continue') return '准备好了'
  if (
    session.value?.status === 'paused'
    && (session.value.pauseReason === 'before_start' || session.value.pauseReason === 'between_actions')
  ) {
    return session.value.flowVersion === 'watch-v1' ? '准备好了' : '开始本组'
  }
  return '继续训练'
})

watch(
  () => session.value?.status,
  (status, previous) => {
    if (!status || status === previous) return
    if (status === 'resting') screenReaderAnnouncement.value = '休息开始'
    else if (status === 'ready_to_continue') screenReaderAnnouncement.value = '休息结束，准备继续训练'
    else if (status === 'paused') screenReaderAnnouncement.value = '训练已暂停'
  },
)
const isRestFocus = computed(() => (
  session.value?.status === 'resting' || session.value?.status === 'ready_to_continue'
))
const coachMessage = computed(() => {
  if (!session.value) return ''
  if (session.value.status === 'resting') return session.value.flowVersion === 'watch-v1'
    ? (session.value.currentSetIndex > 0 ? '先放松呼吸。休息结束后自动倒数，开始下一组。' : '先放松呼吸。接下来换动作，准备好后再开始。')
    : '先放松呼吸。倒计时结束后，由你决定什么时候继续。'
  if (session.value.status === 'countdown') return '调整好姿势，马上开始。'
  if (session.value.status === 'ready_to_continue') return '休息结束了。确认准备好，再开始下一组。'
  if (session.value.status === 'active') return 'TrainPal 会替你记住进度，你只需要专注完成这一组。'
  if (session.value.pauseReason === 'recovered') return '训练进度已经找回，准备好再继续。'
  if (session.value.pauseReason === 'between_actions') return '上一个动作已记下，准备好再进入下一项。'
  return '按自己的节奏来，训练进度会留在这里。'
})
const petState = computed(() => derivePetState({
  sessionStatus: session.value?.status ?? null,
  pauseReason: session.value?.pauseReason ?? null,
  outcome: training.lastRecord?.outcome ?? null,
}))
const currentCoachStyleId = computed(() => (
  session.value?.coachStyleId ?? training.lastRecord?.coachStyleId ?? null
))

const presentCoachEvent = (event: CoachMotionEvent): void => {
  if (!currentCoachStyleId.value) return
  cueSequence += 1
  coachCue.value = { sequence: cueSequence, event }
}

const presentTrainingEvents = (result: TrainingEngineResult): void => {
  if (!result.ok || !currentCoachStyleId.value) return
  const eventTypes = new Set(result.events.map((event) => event.type))
  if (eventTypes.has('session.completed')) presentCoachEvent('session_completed')
  else if (eventTypes.has('set.completed')) presentCoachEvent('set_completed')
  else if (eventTypes.has('set.started')) presentCoachEvent('set_started')
}

watch(
  () => [
    session.value?.status ?? null,
    session.value?.restEndsAt ?? null,
    restRemainingSeconds.value,
    session.value?.coachStyleId ?? null,
  ] as const,
  ([status, restEndsAt, remaining, coachStyleId]) => {
    if (
      status !== 'resting'
      || !restEndsAt
      || coachStyleId !== 'challenger'
      || remaining <= 0
      || remaining > 10
      || countdownRestKey === restEndsAt
    ) return
    countdownRestKey = restEndsAt
    presentCoachEvent('rest_final_countdown')
  },
  { immediate: true },
)

const formatDuration = (seconds: number): string => {
  const safe = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(safe / 60)
  return `${minutes}:${String(safe % 60).padStart(2, '0')}`
}

const syncVideo = async (): Promise<void> => {
  await nextTick()
  const element = video.value
  const range = segment.value
  if (!element || !range) return
  if (document.hidden || leavePause) { element.pause(); return }
  const looping = session.value?.flowVersion === 'watch-v1' && session.value.status === 'active'
  if (looping && (element.ended || element.currentTime >= range.end_seconds)) {
    element.currentTime = range.start_seconds
  } else if (element.ended) {
    element.pause()
    return
  }
  if (element.currentTime >= range.end_seconds) {
    element.currentTime = range.end_seconds
    element.pause()
    return
  }
  if (element.currentTime < range.start_seconds) {
    element.currentTime = range.start_seconds
  }
  if (
    session.value?.status === 'active'
    && !training.commandLocked
    && element.currentTime < range.end_seconds
  ) {
    await element.play().catch(() => undefined)
  } else {
    element.pause()
  }
}

const keepVideoInSegment = (): void => {
  const element = video.value
  const range = segment.value
  if (!element || !range) return
  if (document.hidden || leavePause) { element.pause(); return }
  if (element.currentTime >= range.end_seconds) {
    if (session.value?.flowVersion === 'watch-v1' && session.value.status === 'active' && !training.commandLocked) {
      element.currentTime = range.start_seconds
      void element.play().catch(() => undefined)
      return
    }
    element.currentTime = range.end_seconds
    element.pause()
    return
  }
  if (element.currentTime < range.start_seconds) {
    element.currentTime = range.start_seconds
    if (session.value?.status === 'active') void element.play().catch(() => undefined)
  }
}

const finishVideoSegment = (): void => {
  const element = video.value
  if (!element) return
  if (document.hidden || leavePause) { element.pause(); return }
  if (session.value?.flowVersion === 'watch-v1' && session.value.status === 'active' && segment.value && !training.commandLocked) {
    element.currentTime = segment.value.start_seconds
    void element.play().catch(() => undefined)
  } else element.pause()
}

const restoreVideoVolume = (): void => {
  if (unduckTimer) clearTimeout(unduckTimer)
  if (video.value && originalVolume !== null) video.value.volume = originalVolume
  originalVolume = null
}

const unlockSound = (): void => {
  if (!soundEnabled.value || !window.AudioContext) return
  try {
    cueAudio ??= new AudioContext()
    void cueAudio.resume().catch(() => undefined)
  } catch {
    // Optional audio must never prevent deterministic workout controls.
    cueAudio = null
  }
}

watch(countdownSeconds, (seconds) => {
  restoreVideoVolume()
  if (!seconds || !soundEnabled.value || !cueAudio || document.hidden) return
  if (video.value) {
    originalVolume = video.value.volume
    video.value.volume = originalVolume * 0.2
  }
  const oscillator = cueAudio.createOscillator()
  const gain = cueAudio.createGain()
  oscillator.frequency.value = seconds === 1 ? 880 : 660
  gain.gain.setValueAtTime(0.12, cueAudio.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.001, cueAudio.currentTime + 0.18)
  oscillator.connect(gain).connect(cueAudio.destination)
  oscillator.start()
  oscillator.stop(cueAudio.currentTime + 0.2)
  oscillator.onended = () => { oscillator.disconnect(); gain.disconnect() }
  unduckTimer = setTimeout(restoreVideoVolume, 250)
})

const toggleSound = (): void => {
  soundEnabled.value = !soundEnabled.value
  if (video.value) video.value.muted = !soundEnabled.value
  if (!soundEnabled.value) restoreVideoVolume()
  else unlockSound()
}

const resolveCurrentMedia = async (): Promise<void> => {
  const sourceId = item.value?.sourceRef?.sourceId
  localMediaUrl.value = null
  localMediaError.value = ''
  localMediaResolving.value = false
  if (!sourceId) return
  if (!isLocalSource.value) {
    if (!analysis.sources.length && !analysis.sourcesLoading) {
      await analysis.loadSources(analysisClient)
    }
    return
  }
  localMediaResolving.value = true
  try {
    const resolved = await localMedia.resolve(sourceId)
    if (item.value?.sourceRef?.sourceId !== sourceId) return
    localMediaUrl.value = resolved
    if (!resolved) localMediaError.value = '本地视频已不可用，请重新选择原文件'
  } catch {
    if (item.value?.sourceRef?.sourceId === sourceId) {
      localMediaError.value = '本地视频读取失败，请重新选择原文件'
    }
  } finally {
    if (item.value?.sourceRef?.sourceId === sourceId) localMediaResolving.value = false
  }
}

const reselectCurrentLocalMedia = async (event: Event): Promise<void> => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  const sourceRef = item.value?.sourceRef
  if (!file || !sourceRef || !isLocalSource.value) return
  const sourceId = sourceRef.sourceId
  localMediaError.value = ''
  if (!SUPPORTED_LOCAL_MEDIA_TYPES.includes(file.type as typeof SUPPORTED_LOCAL_MEDIA_TYPES[number])) {
    localMediaError.value = '请选择 MP4、MOV 或 WebM 视频'
    return
  }
  try {
    const durationSeconds = await probeVideoDuration(file)
    if (item.value?.sourceRef?.sourceId !== sourceId) return
    if (
      (!sourceRef.localMedia || !fingerprintMatches(sourceRef.localMedia, file, durationSeconds))
      && !window.confirm('文件信息与原视频不一致，确认仍使用这个文件吗？')
    ) {
      localMediaError.value = '没有替换原视频，训练进度保持不变'
      return
    }
    await localMedia.importFile({ file, durationSeconds, sourceId })
    if (item.value?.sourceRef?.sourceId !== sourceId) return
    localMediaUrl.value = localMedia.urlFor(sourceId)
    mediaLoadFailed.value = false
    await syncVideo()
  } catch {
    if (item.value?.sourceRef?.sourceId === sourceId) {
      localMediaError.value = '无法读取这个视频，请重新选择'
    }
  }
}

watch(
  () => [session.value?.status, item.value?.id, training.commandLocked],
  () => { void syncVideo() },
)

watch(
  () => item.value?.id,
  () => {
    mediaLoadFailed.value = false
    void resolveCurrentMedia()
  },
)

const run = async (
  operation: () => Promise<TrainingEngineResult>,
): Promise<TrainingEngineResult | null> => {
  if (commandPending.value || leavePause) return null
  commandPending.value = true
  try {
    const result = await operation()
    presentTrainingEvents(result)
    await syncVideo()
    return result
  } finally {
    commandPending.value = false
  }
}

const startOrContinue = (): Promise<TrainingEngineResult | null> => run(() => {
  unlockSound()
  if ((session.value?.pausedRestSeconds ?? 0) > 0) return training.startSet()
  if (session.value?.status === 'ready_to_continue' || session.value?.status === 'resting') {
    return training.continueRest()
  }
  if (session.value?.flowVersion === 'watch-v1'
    && (session.value.pendingCountdown || ['before_start', 'between_actions'].includes(session.value.pauseReason ?? ''))) {
    return training.prepareSet()
  }
  return training.startSet()
})

const pause = (): Promise<TrainingEngineResult | null> => run(() => training.pause('user'))
const completeSet = (): Promise<TrainingEngineResult | null> => run(() => training.completeSet())
const continueEarly = (): Promise<TrainingEngineResult | null> => run(() => training.continueRest())
const reloadAfterConflict = (): Promise<TrainingEngineResult | null> => run(() => training.restore())

const endEarly = async (): Promise<void> => {
  if (!window.confirm('提前结束后只记录实际完成量，确定结束吗？')) return
  await run(() => training.endEarly())
}

const skipRemaining = async (): Promise<void> => {
  if (commandPending.value || leavePause) return
  commandPending.value = true
  const result = await training.skipAction()
  commandPending.value = false
  if (!result.ok && result.code === 'end_confirmation_required') {
    await endEarly()
    return
  }
  await syncVideo()
}

const pauseForLeave = (): Promise<void> => {
  video.value?.pause()
  restoreVideoVolume()
  leavePause ??= training.pauseForLeave().finally(() => { leavePause = null })
  return leavePause
}

const handleVisibility = (): void => {
  if (document.hidden) void pauseForLeave()
}

const handlePageHide = (): void => {
  void pauseForLeave()
}

onBeforeRouteLeave(async () => {
  await pauseForLeave()
  return true
})

onMounted(async () => {
  await training.restore()
  await resolveCurrentMedia()
  document.addEventListener('visibilitychange', handleVisibility)
  window.addEventListener('pagehide', handlePageHide)
  ticker = setInterval(() => {
    nowMilliseconds.value = Date.now()
    const current = training.session
    const expiredRest = current?.status === 'resting'
      && current.restEndsAt !== null
      && Date.now() >= Date.parse(current.restEndsAt)
    if (
      !commandPending.value
      && !leavePause
      && !document.hidden
      && !training.commandLocked
      && (current?.status === 'active' || current?.status === 'countdown' || expiredRest)
    ) {
      void run(() => training.tick())
    }
  }, 1_000)
  await syncVideo()
})

onBeforeUnmount(() => {
  restoreVideoVolume()
  void cueAudio?.close()
  if (ticker) clearInterval(ticker)
  document.removeEventListener('visibilitychange', handleVisibility)
  window.removeEventListener('pagehide', handlePageHide)
})
</script>

<template>
  <main
    class="training-page"
    :class="session ? `training-page--${session.status}` : ''"
  >
    <header class="training-header">
      <RouterLink to="/plan">← 返回方案</RouterLink>
      <div>
        <button type="button" @click="library.setPetVisible(!library.preferences.petVisible)">
          {{ library.preferences.petVisible ? '隐藏 TrainPal' : '显示 TrainPal' }}
        </button>
        <span class="status-pill"><i aria-hidden="true"></i>{{ statusLabel }}</span>
      </div>
    </header>

    <section v-if="!session && training.lastRecord" class="terminal-card">
      <p class="eyebrow">TrainPal · 训练已保存</p>
      <h1>{{ training.lastRecord.outcome === 'completed' ? '训练完成' : '已提前结束' }}</h1>
      <CoachMotion
        v-if="training.lastRecord.outcome === 'completed'"
        state="completed"
        :style-id="training.lastRecord.coachStyleId"
        :cue="coachCue"
        :visible="library.preferences.petVisible"
      />
      <div class="terminal-metrics">
        <span><b>{{ training.lastRecord.completedActionCount }}</b> 完成动作</span>
        <span><b>{{ formatDuration(training.lastRecord.trainingDurationSeconds) }}</b> 训练时长</span>
        <span><b>约 {{ training.lastRecord.calorie.value }}</b> 千卡</span>
      </div>
      <p>实际完成量已保存到本机，TrainPal 会在结果页等你。</p>
      <RouterLink class="primary-link" :to="`/result/${training.lastRecord.id}`">查看训练结果</RouterLink>
    </section>

    <section v-else-if="!session" class="training-empty">
      <p class="eyebrow">TrainPal</p>
      <h1>还没有未完成训练</h1>
      <p>先选好一份方案，再和 TrainPal 开始训练。</p>
      <RouterLink class="primary-link" to="/plan">返回方案</RouterLink>
    </section>

    <template v-else-if="item">
      <section class="session-title" aria-labelledby="current-action-title">
        <div>
          <p class="action-position">{{ actionPosition }}</p>
          <p class="eyebrow">{{ session.plan.name }}</p>
          <h1 id="current-action-title">{{ item.name }}</h1>
          <a
            v-if="originalUrl"
            class="original-video-link"
            :href="originalUrl"
            target="_blank"
            rel="noopener noreferrer"
          >
            查看原视频
          </a>
        </div>
        <div class="set-counter">
          <b>{{ setNumber }}</b>
          <span>/ {{ targetSets }} 组</span>
        </div>
      </section>

      <div class="session-layout">
        <section class="media-stage" :class="{ 'no-video': !item.sourceRef }" aria-label="当前动作参考视频">
          <video
            v-if="hasPlayableVideo"
            ref="video"
            :src="mediaUrl ?? undefined"
            playsinline
            controls
            :muted="!soundEnabled"
            preload="metadata"
            @loadedmetadata="syncVideo"
            @timeupdate="keepVideoInSegment"
            @ended="finishVideoSegment"
            @error="mediaLoadFailed = true"
          />
          <div
            v-else-if="item.sourceRef && (analysis.sourcesLoading || localMediaResolving)"
            class="media-placeholder"
          >
            <span>正在读取{{ isLocalSource ? '本地' : '参考' }}视频…</span>
          </div>
          <div v-else-if="item.sourceRef" class="media-placeholder" role="status">
            <span class="no-video-mark">视频暂不可用</span>
            <strong>{{ isLocalSource ? '本地视频已不可用' : '参考视频暂时不可用' }}</strong>
            <small>{{ localMediaError || '可以继续训练，不影响进度记录' }}</small>
            <label v-if="isLocalSource" class="reselect-local-media">
              重新选择原视频
              <input
                type="file"
                :accept="SUPPORTED_LOCAL_MEDIA_TYPES.join(',')"
                @change="reselectCurrentLocalMedia"
              />
            </label>
          </div>
          <div v-else class="media-placeholder">
            <span class="no-video-mark">按自己的节奏练</span>
            <strong>这个动作没有参考视频</strong>
            <small>按自己的节奏完成本组即可</small>
          </div>
          <small
            v-if="isLocalSource && localMedia.current?.sourceId === item.sourceRef?.sourceId && localMedia.storageMessage"
            class="local-storage-message"
          >
            {{ localMedia.storageMessage }}
          </small>
          <div class="stage-badge">{{ mediaBadge }}</div>
        </section>

        <section class="training-console" :class="{ 'training-console--rest': isRestFocus }">
          <p class="tp-visually-hidden" aria-live="polite" aria-atomic="true">
            {{ screenReaderAnnouncement }}
          </p>

          <div v-if="isRestFocus" class="rest-focus">
            <CoachMotion
              class="training-pet training-pet--rest"
              :style-id="session.coachStyleId"
              :state="petState"
              :cue="coachCue"
              :visible="library.preferences.petVisible"
            />
            <div class="rest-focus__copy">
              <span>TrainPal · {{ statusLabel }}</span>
              <strong v-if="session.status === 'resting'">{{ formatDuration(restRemainingSeconds) }}</strong>
              <strong v-else>准备好了？</strong>
              <p>{{ coachMessage }}</p>
            </div>
          </div>

          <template v-else>
            <div class="coach-strip">
              <CoachMotion
                class="training-pet"
                :style-id="session.coachStyleId"
                :state="petState"
                :cue="coachCue"
                :visible="library.preferences.petVisible"
              />
              <div>
                <span>TrainPal 陪你练</span>
                <SourceTips v-if="session.status === 'active' && item.sourceTips?.length" :tips="item.sourceTips" />
                <p v-else>{{ coachMessage }}</p>
              </div>
            </div>
            <div class="status-readout">
              <span>{{ statusLabel }}</span>
              <strong v-if="session.status === 'countdown'" aria-live="polite">{{ countdownSeconds }}</strong>
              <strong v-else-if="item.mode === 'duration'">{{ formatDuration(durationRemainingSeconds) }}</strong>
              <strong v-else>{{ item.reps.value }} 次</strong>
              <small>
                已完成 {{ progress?.completedSets ?? 0 }} / {{ targetSets }} 组
                <template v-if="item.weightKg.value !== null"> · {{ item.weightKg.value }} kg</template>
              </small>
            </div>
          </template>

          <button
            v-if="session.status === 'countdown'"
            type="button" class="primary-action" :disabled="commandPending || training.commandLocked" @click="pause"
          >
            先暂停
          </button>
          <button
            v-else-if="session.status === 'paused' || session.status === 'ready_to_continue'"
            type="button"
            class="primary-action"
            :disabled="commandPending || training.commandLocked"
            @click="startOrContinue"
          >
            {{ startActionLabel }}
          </button>
          <button
            v-else-if="session.status === 'resting'"
            type="button"
            class="primary-action rest-action"
            :disabled="commandPending || training.commandLocked"
            @click="continueEarly"
          >
            提前继续
          </button>
          <button
            v-else-if="item.mode === 'reps'"
            type="button"
            class="primary-action"
            :disabled="commandPending || training.commandLocked"
            @click="completeSet"
          >
            完成本组
          </button>
          <p v-else class="active-duration-note" role="status">倒计时结束后自动进入休息</p>

          <details class="more-actions">
            <summary>更多训练操作</summary>
            <div class="secondary-actions">
              <button type="button" :aria-pressed="!soundEnabled" @click="toggleSound">{{ soundEnabled ? '静音' : '开启声音' }}</button>
              <button
                v-if="session.status === 'active' || session.status === 'resting'"
                type="button"
                class="pause-training"
                :disabled="commandPending || training.commandLocked"
                @click="pause"
              >
                暂停
              </button>
              <button
                v-if="session.status === 'active'"
                type="button"
                :disabled="commandPending || training.commandLocked"
                @click="skipRemaining"
              >
                跳过剩余组
              </button>
              <button type="button" class="danger" :disabled="commandPending || training.commandLocked" @click="endEarly">
                提前结束
              </button>
            </div>
          </details>
        </section>
      </div>

      <p v-if="training.errorMessage" class="training-error" role="alert">
        {{ training.errorMessage }}
        <button
          v-if="training.commandLocked"
          type="button"
          :disabled="commandPending"
          @click="reloadAfterConflict"
        >
          重新加载最新进度
        </button>
      </p>
    </template>
  </main>
</template>

<style scoped>
.training-page {
  width: min(100%, 1080px);
  min-height: 100dvh;
  margin: auto;
  padding:
    max(16px, env(safe-area-inset-top))
    clamp(14px, 4vw, 32px)
    max(24px, env(safe-area-inset-bottom));
  color: var(--tp-training-ink);
  background:
    var(--tp-training-canvas);
}
.training-header,
.session-title,
.terminal-metrics,
.secondary-actions { display: flex; align-items: center; }
.training-header { justify-content: space-between; gap: 12px; margin-bottom: clamp(18px, 4vw, 34px); }
.training-header a { display: inline-grid; min-height: 44px; place-items: center; color: var(--tp-training-ink); font-size: 12px; font-weight: 800; text-decoration: none; }
.training-header > div { display: flex; align-items: center; gap: 8px; }
.training-header button { min-height: 44px; padding: 0 8px; border: 0; color: var(--tp-muted); background: transparent; font-size: 11px; }
.status-pill { display: inline-flex; min-height: 32px; align-items: center; gap: 7px; padding: 0 10px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-training-ink); background: rgb(247 243 233 / 6%); font-size: 11px; letter-spacing: .05em; }
.status-pill i { width: 7px; height: 7px; border-radius: 50%; background: var(--tp-secondary); box-shadow: 0 0 0 4px rgb(165 186 99 / 12%); }
.eyebrow { margin: 0; color: var(--tp-secondary); font: 700 11px/1 var(--font-display), var(--font-cn); letter-spacing: .14em; text-transform: uppercase; }
.action-position { margin: 0 0 7px; color: var(--tp-muted); font-size: 11px; font-weight: 800; }
.session-title { justify-content: space-between; gap: 16px; max-width: 760px; margin-bottom: 16px; }
.session-title h1 { margin: 7px 0 0; color: var(--tp-training-ink); font: 700 clamp(26px, 6vw, 38px)/1.3 var(--font-display), var(--font-cn); letter-spacing: -.025em; }
.original-video-link { display: inline-flex; min-width: 44px; min-height: 44px; align-items: center; color: var(--tp-secondary); font-size: 11px; font-weight: 800; text-decoration: none; }
.set-counter { flex: 0 0 auto; color: var(--tp-muted); text-align: right; }
.set-counter b { color: var(--tp-training-ink); font: 700 clamp(26px, 6vw, 36px)/1.1 var(--font-display); }
.set-counter span { font-size: 11px; }
.session-layout { display: grid; gap: 14px; }
.media-stage { position: relative; overflow: hidden; height: clamp(218px, 41dvh, 460px); border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-media-canvas); box-shadow: var(--tp-shadow-soft); }
.media-stage:has(.media-placeholder) { height: 180px; }
.media-stage video { width: 100%; height: 100%; object-fit: contain; }
.media-placeholder { display: grid; height: 100%; place-content: center; gap: 8px; padding: 20px; color: var(--tp-muted); background: var(--tp-surface-raised); text-align: center; }
.media-placeholder strong { color: var(--tp-training-ink); font-size: 18px; }
.media-placeholder small { font-size: 11px; }
.reselect-local-media { position: relative; display: inline-grid; min-height: 44px; margin-top: 6px; place-items: center; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-secondary); font-size: 11px; font-weight: 800; cursor: pointer; }
.reselect-local-media:focus-within { outline: 2px solid var(--tp-focus); outline-offset: 3px; }
.reselect-local-media input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.local-storage-message { position: absolute; right: 12px; bottom: 12px; left: 12px; z-index: 2; padding: 8px 10px; border-radius: 10px; color: #FFFEFA; background: rgb(14 19 17 / 86%); line-height: 1.5; text-align: center; }
.no-video-mark { color: var(--tp-primary-readable); font: 700 12px/1 var(--font-display); letter-spacing: .16em; }
.stage-badge { position: absolute; top: 12px; left: 12px; padding: 7px 10px; border: 1px solid var(--tp-line); border-radius: 12px; color: #FFFEFA; background: rgb(14 19 17 / 82%); font-size: 11px; backdrop-filter: blur(10px); }
.training-console { position: relative; padding: 16px; border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-training-surface); box-shadow: none; }
.coach-strip { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 12px; margin-bottom: 10px; padding: 10px 12px; border-radius: 18px; background: rgb(165 186 99 / 9%); }
.coach-strip :deep(.trainpal-coach__image) { width: clamp(58px, 18vw, 78px); }
.coach-strip span,
.rest-focus__copy > span { color: var(--tp-secondary); font: 700 11px/1 var(--font-display), var(--font-cn); letter-spacing: .12em; }
.coach-strip p { margin: 5px 0 0; color: var(--tp-training-ink); font-size: 12px; line-height: 1.5; }
.coach-strip:not(:has(.trainpal-coach)) { grid-template-columns: 1fr; }
.coach-strip :deep(.source-tips) { margin: 4px 0 0; padding: 0; background: transparent; }
.status-readout { display: grid; justify-items: center; margin: 8px 0 14px; }
.status-readout span { color: var(--tp-muted); font-size: 11px; font-weight: 700; }
.status-readout strong { margin: 5px 0; color: var(--tp-training-ink); font: 700 clamp(52px, 16vw, 76px)/.85 var(--font-display); }
.status-readout small { color: var(--tp-muted); font-size: 11px; }
.rest-focus { display: grid; justify-items: center; gap: 2px; padding: 4px 0 16px; text-align: center; }
.training-pet--rest :deep(.trainpal-coach__image) { width: clamp(104px, 34vw, 152px); }
.rest-focus__copy strong { display: block; margin: 9px 0 8px; color: var(--tp-training-ink); font: 700 clamp(58px, 19vw, 92px)/.82 var(--font-display), var(--font-cn); }
.rest-focus__copy p { max-width: 320px; margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.55; }
.primary-action,
.primary-link { display: grid; width: 100%; min-height: 52px; place-items: center; border: 1px solid var(--tp-primary); border-radius: 12px; color: #FFFEFA; background: var(--tp-primary-readable); box-shadow: var(--tp-shadow-soft); font-weight: 800; text-decoration: none; }
.rest-action { border-color: var(--tp-secondary); color: var(--tp-training-canvas); background: var(--tp-secondary); box-shadow: 0 12px 28px rgb(165 186 99 / 20%); }
.primary-action:disabled { opacity: .5; }
.active-duration-note { min-height: 44px; margin: 0; color: var(--tp-muted); font-size: 11px; line-height: 44px; text-align: center; }
.more-actions { margin-top: 6px; }
 .more-actions summary { display: flex; min-height: 44px; align-items: center; justify-content: center; color: var(--tp-muted); font-size: 11px; font-weight: 700; cursor: pointer; list-style: none; }
.more-actions summary::-webkit-details-marker { display: none; }
.more-actions summary::after { content: '＋'; margin-left: 6px; }
.more-actions[open] summary::after { content: '－'; }
.secondary-actions { justify-content: center; flex-wrap: wrap; gap: 5px; padding-top: 4px; border-top: 1px solid var(--tp-line); }
.secondary-actions button { min-height: 44px; padding: 0 10px; border: 0; color: var(--tp-muted); background: transparent; font-size: 11px; }
.secondary-actions .danger { color: var(--tp-primary-readable); }
.training-error { margin: 12px 0 0; padding: 10px; border: 1px solid rgb(239 116 91 / 24%); border-radius: 12px; color: var(--tp-danger); background: rgb(239 116 91 / 8%); font-size: 11px; text-align: center; }
.training-error button { display: block; min-height: 44px; margin: 6px auto 0; padding: 0 12px; border: 1px solid rgb(239 116 91 / 35%); border-radius: 12px; color: var(--tp-danger); background: transparent; font-weight: 700; }
.terminal-card,
.training-empty { width: min(100%, 520px); margin: 12vh auto 0; padding: clamp(24px, 8vw, 42px); border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-training-surface); box-shadow: none; text-align: center; }
.terminal-card h1,
.training-empty h1 { margin: 10px 0; color: var(--tp-training-ink); font: 700 clamp(28px, 7vw, 40px)/1.3 var(--font-display), var(--font-cn); }
.terminal-card > p:not(.eyebrow),
.training-empty > p:not(.eyebrow) { color: var(--tp-muted); font-size: 12px; }
.terminal-metrics { justify-content: center; gap: clamp(12px, 6vw, 30px); margin: 24px 0; }
.terminal-metrics span { color: var(--tp-muted); font-size: 11px; }
.terminal-metrics b { display: block; color: var(--tp-training-ink); font: 700 28px/1 var(--font-display); }
.terminal-card :deep(.trainpal-coach) { margin: 18px auto -8px; }

@media (min-width: 800px) {
  .session-title { max-width: none; }
  .session-layout { grid-template-columns: minmax(0, 1.35fr) minmax(310px, .65fr); align-items: stretch; gap: 22px; }
  .media-stage { height: min(62dvh, 620px); }
  .training-console { display: flex; min-height: 480px; flex-direction: column; justify-content: center; padding: 24px; }
  .more-actions { margin-top: 12px; }
}
</style>
