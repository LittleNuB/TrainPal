<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { requestPlaybackChoice, transcribeVoiceCommand } from '@/api/playback-client'
import { currentPlaybackRange, playbackFingerprint, playbackOptions, type PlaybackOption } from '@/domain/playback'
import { useTrainingStore } from '@/stores/training'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { captureVoiceCommand } from '@/training/voice-command'

const props = defineProps<{ video: HTMLVideoElement | null }>()
const emit = defineEmits<{ busy: [value: boolean]; resume: [] }>()
const training = useTrainingStore()
const draft = useDraftStore()
const library = useLibraryStore()
const item = computed(() => training.currentItem)
const instruction = ref('')
const message = ref('')
const phase = ref<'idle' | 'recording' | 'finding' | 'preview' | 'saving'>('idle')
const proposal = ref<PlaybackOption | null>(null)
let controller: AbortController | null = null
let generation = 0
let binding: { sessionId: string; revision: number; itemId: string; fingerprint: string } | null = null
let previewing = false
let timeout: ReturnType<typeof setTimeout> | null = null
const locked = computed(() => phase.value !== 'idle')
watch(locked, (value) => emit('busy', value))
const currentBinding = (): boolean => Boolean(binding && training.session
  && training.session.sessionId === binding.sessionId && training.session.revision === binding.revision
  && item.value?.id === binding.itemId && playbackFingerprint(item.value) === binding.fingerprint
  && training.session.status === 'paused' && !training.commandLocked)

const stopPreview = () => { if (previewing) props.video?.pause(); previewing = false }
const cancel = () => {
  generation++
  controller?.abort()
  if (timeout) clearTimeout(timeout)
  stopPreview()
  proposal.value = null
  if (phase.value === 'saving') {
    message.value = '正在完成保存，训练会保持暂停。'
  } else {
    phase.value = 'idle'
    message.value = '原片段保持不变，准备好后可继续训练。'
  }
}

const pauseAndBind = async (): Promise<boolean> => {
  props.video?.pause()
  if (training.session?.status !== 'paused') {
    const paused = await training.pause('user')
    if (!paused.ok) { message.value = paused.message; return false }
  }
  if (!training.session || !item.value || training.commandLocked) return false
  binding = { sessionId: training.session.sessionId, revision: training.session.revision,
    itemId: item.value.id, fingerprint: playbackFingerprint(item.value) }
  return true
}

const find = async (voice = false) => {
  if (locked.value || !item.value || (!voice && !instruction.value.trim())) return
  const request = ++generation
  phase.value = 'finding'
  if (!await pauseAndBind()) { phase.value = 'idle'; return }
  if (request !== generation) return
  const requestController = new AbortController()
  controller = requestController
  const signal = requestController.signal
  message.value = ''
  const requestTimeout = setTimeout(() => requestController.abort(), voice ? 42_000 : 17_000)
  timeout = requestTimeout
  try {
    if (voice) {
      phase.value = 'recording'
      const audio = await captureVoiceCommand(signal)
      if (request !== generation || !currentBinding()) return
      phase.value = 'finding'
      instruction.value = await transcribeVoiceCommand(audio, signal)
    }
    if (request !== generation || !currentBinding()) return
    if (/疼|痛|受伤|不舒服|眩晕|头晕|难受|pain|hurt|dizz/i.test(instruction.value)) {
      message.value = '请先停止训练。TrainPal 不能判断身体不适的原因，也不会自动为你调整动作。'
      phase.value = 'idle'
      return
    }
    phase.value = 'finding'
    const options = playbackOptions(item.value!)
    const choice = await requestPlaybackChoice(item.value!.name, instruction.value.trim(), options, signal)
    if (request !== generation || !currentBinding()) return
    proposal.value = options.find((option) => option.id === choice) ?? null
    phase.value = proposal.value ? 'preview' : 'idle'
    if (!proposal.value) message.value = '已有来源里还找不到这段讲解。原片段保持不变，可以手动播放。'
  } catch (error) {
    if (request === generation) {
      message.value = signal.aborted ? '查找已停止，训练保持暂停。' : error instanceof Error ? error.message : '查找未完成，请重试'
      phase.value = 'idle'
    }
  } finally {
    clearTimeout(requestTimeout)
    if (request === generation && !currentBinding()) cancel()
  }
}

const preview = async () => {
  const media = props.video
  if (!currentBinding() || !proposal.value || !media) return
  media.currentTime = proposal.value.start_seconds
  previewing = true
  try { await media.play() } catch { previewing = false; message.value = '视频暂时无法播放，可以重新选择原视频。' }
}
const onVideoTime = () => {
  if (previewing && proposal.value && props.video && props.video.currentTime >= proposal.value.end_seconds) stopPreview()
}
watch(() => props.video, (next, previous) => {
  previous?.removeEventListener('timeupdate', onVideoTime)
  next?.addEventListener('timeupdate', onVideoTime)
})

const save = async (restore = false) => {
  if (!item.value || training.commandLocked || phase.value === 'saving' || (restore && locked.value)) return
  const request = ++generation
  if (restore) {
    phase.value = 'finding'
    const paused = await pauseAndBind()
    if (request !== generation) return
    if (!paused || !currentBinding()) { phase.value = 'idle'; return }
  } else if (!proposal.value || !currentBinding()) { cancel(); return }
  phase.value = 'saving'
  const selected = { ...binding!, expectedRevision: binding!.revision,
    range: restore ? null : { start_seconds: proposal.value!.start_seconds, end_seconds: proposal.value!.end_seconds } }
  stopPreview()
  try {
    await draft.flushPersist()
    if (request !== generation || !currentBinding()) {
      message.value = '训练状态已变化，未保存这次调整，请重新选择。'
      return
    }
    const result = await training.selectPlayback(selected)
    if (!result.ok) { message.value = result.message; return }
    if (draft.syncSavedPlaybackSelection({ ...selected,
      planId: result.session?.plan.sourcePlanId ?? null })) await draft.flushPersist()
    await library.refreshHistory()
    proposal.value = null
    message.value = restore ? '已恢复完整教学，准备好后继续。' : '已记住这个片段，下次复练也会使用。'
    if (!restore && request === generation && !document.hidden
      && training.session?.sessionId === selected.sessionId
      && training.session.status === 'paused' && item.value?.id === selected.itemId
      && !training.commandLocked) emit('resume')
  } catch { message.value = '片段未能完整保存，训练保持暂停，请重试。' }
  finally { phase.value = 'idle' }
}
const rewind = () => {
  const media = props.video
  if (!media || !item.value) return
  const range = currentPlaybackRange(item.value)
  if (range) media.currentTime = Math.max(range.start_seconds, media.currentTime - 5)
}
const hide = () => { if (document.hidden) cancel() }
document.addEventListener('visibilitychange', hide)
watch(() => item.value?.id, () => { cancel(); instruction.value = ''; message.value = '' })
onBeforeUnmount(() => { cancel(); props.video?.removeEventListener('timeupdate', onVideoTime); document.removeEventListener('visibilitychange', hide) })
</script>

<template>
  <details v-if="item?.sourceRef" class="playback-controls" @toggle="($event.target as HTMLDetailsElement).open || cancel()">
    <summary>调整观看片段</summary>
    <p>告诉 TrainPal 想看哪一段。查找时会暂停训练，确认后再继续。</p>
    <form @submit.prevent="find()">
      <label for="clip-request">想怎么调整？</label>
      <input id="clip-request" v-model="instruction" maxlength="240" placeholder="例如：重看呼吸讲解" :disabled="locked" />
      <div class="playback-buttons">
        <button type="submit" :disabled="locked || !instruction.trim() || training.commandLocked">查找片段</button>
        <button type="button" :disabled="locked || training.commandLocked" @click="find(true)">说一句</button>
      </div>
    </form>
    <small>说完自动结束，最长 15 秒。录音发送至现有语音服务识别，本机临时文件处理后删除；不持续监听。</small>
    <p v-if="phase === 'recording'" role="status">正在听…说完后稍停一下。</p>
    <p v-else-if="phase === 'finding'" role="status">训练已暂停，正在找片段…</p>
    <div v-if="proposal" class="clip-proposal">
      <strong>{{ proposal.label }}</strong>
      <p>{{ proposal.start_seconds.toFixed(1) }}–{{ proposal.end_seconds.toFixed(1) }} 秒</p>
      <div class="playback-buttons"><button type="button" :disabled="phase === 'saving' || !video" @click="preview">预览片段</button><button type="button" :disabled="phase === 'saving'" @click="save()">用这段并继续</button></div>
    </div>
    <button v-if="locked && phase !== 'saving'" type="button" @click="cancel">取消，保持暂停</button>
    <p v-if="message" role="status">{{ message }}</p>
    <div v-if="!locked" class="playback-buttons"><button type="button" :disabled="!video" @click="rewind">回退 5 秒</button><button v-if="item.playbackSelection" type="button" @click="save(true)">恢复完整教学</button></div>
  </details>
</template>

<style scoped>
.playback-controls { margin-top: 12px; padding: 0 12px 8px; border: 1px solid var(--tp-line); border-radius: 12px; }
summary, button { min-height: 44px; cursor: pointer; }
summary { display: flex; align-items: center; font-size: 13px; }
p, label { font-size: 13px; line-height: 1.6; }
small { display: block; color: var(--tp-muted); font-size: 11px; line-height: 1.5; }
input { width: 100%; min-height: 44px; margin-top: 6px; padding: 8px; border: 1px solid var(--tp-line); border-radius: 8px; background: var(--tp-surface-raised); color: var(--tp-ink); }
.playback-buttons { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
button { padding: 8px 12px; border: 1px solid var(--tp-line); border-radius: 8px; background: var(--tp-surface-raised); color: var(--tp-ink); }
button:disabled { opacity: .5; cursor: default; }
.clip-proposal { padding: 12px 0; }
</style>
