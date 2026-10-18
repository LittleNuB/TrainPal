<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import CoachMotion from './CoachMotion.vue'
import type { PetState } from './pet-state'
import type { CoachMotionCue } from '@/domain/coach'
import { requestPlaybackChoice, transcribeVoiceCommand } from '@/api/playback-client'
import { currentPlaybackRange, playbackFingerprint, playbackOptions, type PlaybackOption } from '@/domain/playback'
import { useTrainingStore } from '@/stores/training'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'
import { captureVoiceCommand } from '@/training/voice-command'
import { installLocalWake, localWakeAvailability, startLocalWake } from '@/training/voice-wake'
import { userMessage } from '@/domain/user-message'

const props = defineProps<{ video: HTMLVideoElement | null; state?: PetState; cue?: CoachMotionCue | null }>()
const emit = defineEmits<{ busy: [value: boolean]; resume: []; handsfree: [value: boolean] }>()
const training = useTrainingStore()
const draft = useDraftStore()
const library = useLibraryStore()
const item = computed(() => training.currentItem)
const instruction = ref('')
const message = ref('')
const phase = ref<'idle' | 'recording' | 'finding' | 'preview' | 'saving'>('idle')
const proposal = ref<PlaybackOption | null>(null)
const expanded = ref(false)
const sheet = ref<HTMLDialogElement | null>(null)
const entry = ref<HTMLButtonElement | null>(null)
const wakeAwake = ref(false)
const noticeMode = ref<'once' | 'wake'>('once')
const textEntry = ref(false)
const voiceNotice = ref(false)
const voiceConsent = ref(false)
const handsfree = ref(false)
const wakeStatus = ref('')
const wakeSetup = ref<'idle' | 'checking' | 'download' | 'installing'>('idle')
let stopWake: (() => void) | null = null
let wakeGeneration = 0
let setupGeneration = 0
let disposed = false
let wakePause: Promise<boolean> | null = null
let wakeItemId: string | undefined
let controller: AbortController | null = null
let generation = 0
let binding: { sessionId: string; revision: number; itemId: string; fingerprint: string } | null = null
let previewing = false
let timeout: ReturnType<typeof setTimeout> | null = null
const locked = computed(() => phase.value !== 'idle')
watch(() => locked.value || expanded.value, (value) => emit('busy', value), { flush: 'sync' })
const listening = computed(() => phase.value === 'recording' || wakeAwake.value)
const title = computed(() => listening.value ? '我在听' : phase.value === 'finding' ? '正在找讲解'
  : phase.value === 'saving' ? '正在保存片段' : proposal.value ? '找到这段讲解'
    : voiceNotice.value && noticeMode.value === 'wake' ? '喊 TrainPal，就能说' : '跟我说')
watch(expanded, async (open) => {
  await nextTick()
  if (disposed || open !== expanded.value) return
  if (open) {
    if (!sheet.value?.open) sheet.value?.showModal()
  } else {
    sheet.value?.close()
    entry.value?.focus({ preventScroll: true })
  }
})
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
  wakeAwake.value = false
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
  expanded.value = true
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
  let handledCommand = false
  try {
    if (voice) {
      phase.value = 'recording'
      const audio = await captureVoiceCommand(signal)
      if (request !== generation || !currentBinding()) return
      phase.value = 'finding'
      instruction.value = await transcribeVoiceCommand(audio, signal)
    }
    if (request !== generation || !currentBinding()) return
    if (await simpleCommand(instruction.value)) { handledCommand = true; phase.value = 'idle'; return }
    if (/疼|痛|受伤|不舒服|眩晕|头晕|难受|pain|hurt|dizz/i.test(instruction.value)) {
      message.value = '请先停止训练。TrainPal 不能判断身体不适的原因，也不会自动为你调整动作。'
      phase.value = 'idle'
      return
    }
    if (!item.value!.sourceRef) { message.value = '这个动作没有教学视频，可以说“继续训练”。'; phase.value = 'idle'; return }
    if (/次数|组数|训练量|休息时长|[加减增少多].*[次组秒]/.test(instruction.value)) {
      message.value = '训练次数、组数和休息时长，请在方案的动作卡片中修改。'
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
      message.value = signal.aborted ? '查找已停止，训练保持暂停。' : userMessage(error, '这次没能完成，请重试，或用文字告诉我。')
      phase.value = 'idle'
    }
  } finally {
    clearTimeout(requestTimeout)
    if (request === generation && !handledCommand && !currentBinding()) cancel()
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
      && !training.commandLocked) resumeTraining()
  } catch { message.value = '片段未能完整保存，训练保持暂停，请重试。' }
  finally { phase.value = 'idle' }
}
const rewind = () => {
  const media = props.video
  const range = item.value && currentPlaybackRange(item.value)
  if (!media || !range) {
    message.value = '这个动作没有可回看的视频，训练保持暂停。'
    return
  }
  media.currentTime = Math.max(range.start_seconds, media.currentTime - 5)
  message.value = media.currentTime === range.start_seconds
    ? '已回到片段开头，准备好后继续训练。'
    : '已回退 5 秒，准备好后继续训练。'
}
const stopListening = () => { wakeGeneration++; stopWake?.(); stopWake = null }
const resumeTraining = () => {
  phase.value = 'idle'
  expanded.value = false
  textEntry.value = false
  voiceNotice.value = false
  wakeAwake.value = false
  emit('resume')
}
const disableHandsfree = () => {
  setupGeneration++
  wakeSetup.value = 'idle'
  handsfree.value = false
  wakeAwake.value = false
  wakeStatus.value = ''
  stopListening()
}
const activateHandsfree = async (download = false) => {
  const attempt = ++setupGeneration
  wakeSetup.value = download ? 'installing' : 'checking'
  if (download && !await installLocalWake()) {
    if (attempt !== setupGeneration || disposed) return
    wakeStatus.value = '语音准备未完成，请重试，或点一下说话。'
    wakeSetup.value = 'download'
    return
  }
  const availability = await localWakeAvailability()
  if (attempt !== setupGeneration || disposed || document.hidden) return
  wakeSetup.value = availability === 'downloadable' ? 'download' : 'idle'
  if (availability === 'available') {
    voiceConsent.value = true
    voiceNotice.value = false
    handsfree.value = true
  } else wakeStatus.value = availability === 'downloadable'
    ? '首次使用需要下载中文语音包。'
    : '这个浏览器暂不支持语音唤醒，可以点一下说话。'
}
const openPanel = async () => {
  expanded.value = true
  return pauseAndBind()
}
const openWakeSettings = async () => {
  if (handsfree.value) { disableHandsfree(); return }
  noticeMode.value = 'wake'
  voiceNotice.value = true
  await openPanel()
}
const speakOnce = async () => {
  noticeMode.value = 'once'
  voiceNotice.value = !voiceConsent.value
  textEntry.value = false
  instruction.value = ''
  message.value = ''
  const request = generation
  const paused = await openPanel()
  if (!paused || request !== generation || !expanded.value || disposed) return
  if (!voiceConsent.value || textEntry.value) return
  voiceNotice.value = false
  textEntry.value = false
  stopListening()
  void find(true)
}
const acceptVoice = () => { voiceConsent.value = true; voiceNotice.value = false; speakOnce() }
const shortcut = (text: string) => { instruction.value = text; void find() }
const simpleCommand = async (text: string): Promise<boolean> => {
  const command = text.replace(/[，,。.!！?？\s]/g, '')
  if (/^(关闭免手模式|关闭语音唤醒)$/.test(command)) { disableHandsfree(); return true }
  if (/^(暂停|暂停训练|休息一下)$/.test(command)) { message.value = '训练已暂停。'; return true }
  if (/^(继续|继续训练)$/.test(command)) { resumeTraining(); return true }
  if (/^(回退五秒|回退5秒|后退五秒|后退5秒)$/.test(command)) { rewind(); return true }
  return false
}
const spoken = async (text: string, listener: number) => {
  const paused = wakePause ? await wakePause : false
  if (listener !== wakeGeneration || document.hidden || disposed) return
  wakeAwake.value = false
  if (!paused || training.commandLocked || wakeItemId !== item.value?.id) { cancel(); listen(); return }
  const command = text.replace(/[，,。.!！?？\s]/g, '')
  expanded.value = true
  if (/^(关闭免手模式|关闭语音唤醒)$/.test(command)) { disableHandsfree(); return }
  if (/^(取消|算了|不用了)$/.test(command)) { close(); return }
  if (/^(确认|用这段|用这段并继续)$/.test(command) && phase.value === 'preview') { await save(); listen(); return }
  if (/^(预览|预览片段)$/.test(command) && phase.value === 'preview') { await preview(); listen(); return }
  if (phase.value === 'preview') { message.value = '说“TrainPal，确认”使用这段，或说“TrainPal，取消”。'; listen(); return }
  if (await simpleCommand(text)) { listen(); return }
  instruction.value = text
  await find()
  listen()
}
const listen = () => {
  stopListening()
  if (!handsfree.value || document.hidden || disposed) return
  if (!['idle', 'preview'].includes(phase.value)) { wakeStatus.value = '正在处理指令…'; return }
  const listener = wakeGeneration
  wakeStatus.value = '正在开启麦克风…'
  try {
    stopWake = startLocalWake({
      onReady: () => { if (listener === wakeGeneration) { wakeAwake.value = false; wakeStatus.value = '喊「TrainPal」就能说' } },
      onWake: () => {
        if (listener !== wakeGeneration) return
        wakeStatus.value = '我在，说吧…'
        message.value = ''
        if (!proposal.value) instruction.value = ''
        wakeAwake.value = true
        voiceNotice.value = false
        textEntry.value = false
        expanded.value = true
        wakeItemId = item.value?.id
        wakePause = phase.value === 'preview' ? Promise.resolve(currentBinding()) : pauseAndBind()
      },
      onCommand: (text) => { void spoken(text, listener) },
      onTimeout: () => { wakeAwake.value = false; message.value = '没听清，再喊一次 TrainPal 或点一下说话。' },
      onError: (text) => { disableHandsfree(); wakeStatus.value = text },
    })
  } catch { disableHandsfree(); wakeStatus.value = '语音唤醒未能开启，可以点一下说话。' }
}
watch(handsfree, (value) => { emit('handsfree', value); listen() })
watch(phase, listen)
const close = () => {
  setupGeneration++
  wakeSetup.value = 'idle'
  cancel()
  expanded.value = false
  textEntry.value = false
  voiceNotice.value = false
  listen()
}
const hide = () => { if (document.hidden) { disableHandsfree(); close() } }
document.addEventListener('visibilitychange', hide)
watch(() => item.value?.id, () => { cancel(); instruction.value = ''; message.value = ''; listen() })
onBeforeUnmount(() => { disposed = true; disableHandsfree(); cancel(); props.video?.removeEventListener('timeupdate', onVideoTime); document.removeEventListener('visibilitychange', hide) })
</script>

<template>
  <section v-if="item" class="playback-controls" aria-label="TrainPal 语音助手">
    <div v-show="!expanded" class="voice-entry-row">
      <div class="voice-shortcut">
        <button ref="entry" type="button" class="voice-entry" aria-label="跟我说" aria-haspopup="dialog" :aria-expanded="expanded" @click="speakOnce">
          <CoachMotion :style-id="training.session?.coachStyleId ?? null" :state="state ?? 'idle'" :cue="cue" :visible="library.preferences.petVisible" />
          <svg aria-hidden="true" viewBox="0 0 24 24"><rect x="9" y="2" width="6" height="13" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" /></svg>
          <span>跟我说</span>
        </button>
        <small>{{ handsfree ? '喊 TrainPal 也可以' : item.sourceRef ? '重看讲解、暂停训练' : '暂停、继续，听你说' }}</small>
      </div>
      <button type="button" class="wake-switch" :aria-pressed="handsfree" :disabled="wakeSetup === 'checking' || wakeSetup === 'installing'" @click="openWakeSettings">
        <i aria-hidden="true" />语音唤醒 · {{ handsfree ? '开' : '关' }}
      </button>
    </div>
    <p v-if="message && !expanded" class="entry-feedback" role="status">{{ message }}</p>
    <p v-if="wakeStatus && !handsfree && !expanded" class="entry-feedback" role="status">{{ wakeStatus }}</p>
    <dialog ref="sheet" class="voice-sheet" aria-labelledby="voice-title" @cancel.prevent="close">
      <div v-if="expanded" class="voice-panel">
        <div class="sheet-handle" aria-hidden="true" />
        <header class="voice-heading">
          <CoachMotion :style-id="training.session?.coachStyleId ?? null" state="paused" :visible="library.preferences.petVisible" />
          <div><small>{{ training.session?.status === 'paused' ? '训练已暂停' : '正在暂停训练…' }}</small><h2 id="voice-title">{{ title }}</h2></div>
          <button type="button" class="close-sheet" aria-label="关闭 TrainPal" autofocus @click="close"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
        </header>
        <template v-if="voiceNotice">
          <p class="voice-help">{{ noticeMode === 'wake' ? '开启后，喊“TrainPal”再说需求。切走页面后需要重新开启。' : item.sourceRef ? '说一句“重看呼吸讲解”，我来帮你找。' : '可以说“暂停”或“继续训练”。' }}</p>
          <div class="voice-actions">
            <template v-if="noticeMode === 'wake'">
              <button v-if="wakeSetup === 'download'" type="button" class="primary" @click="activateHandsfree(true)">下载并开启</button>
              <button v-else type="button" class="primary" :disabled="wakeSetup !== 'idle'" @click="activateHandsfree()">{{ wakeSetup === 'checking' ? '正在检查…' : wakeSetup === 'installing' ? '正在准备语音…' : '开启语音唤醒' }}</button>
              <button type="button" :disabled="locked || wakeSetup === 'installing'" @click="acceptVoice">只说这一次</button>
            </template>
            <button v-else type="button" class="primary" :disabled="training.commandLocked" @click="acceptVoice">开始说话</button>
            <button type="button" @click="voiceNotice = false; textEntry = true">用文字</button>
          </div>
          <details class="voice-privacy"><summary>语音与隐私</summary><p>语音唤醒在设备上识别，只发送查找片段的指令。单次说话会上传这次录音进行识别，处理后删除。</p></details>
        </template>
        <div v-else-if="listening" class="listening-state">
          <div class="voice-wave" aria-hidden="true"><i v-for="n in 15" :key="n" :style="{ '--bar': (n % 5) + 1 }" /></div>
          <p role="status">我在听，说完稍停一下。</p>
          <p v-if="instruction" class="heard-instruction">{{ instruction }}</p>
          <button type="button" @click="cancel(); textEntry = true; stopListening()">用文字</button>
        </div>
        <div v-else-if="phase === 'finding' || phase === 'saving'" class="finding-state" role="status">
          <span class="finding-dots" aria-hidden="true">···</span>
          <p>{{ phase === 'saving' ? '正在保存你的选择…' : '正在找这段讲解…' }}</p>
        </div>
        <template v-else-if="proposal">
          <div class="clip-proposal">
            <button type="button" class="preview-tile" aria-label="预览片段" :disabled="!video" @click="preview">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 5 10 7-10 7Z" /></svg>
            </button>
            <div><strong>{{ proposal.label }}</strong><p>{{ proposal.start_seconds.toFixed(1) }}–{{ proposal.end_seconds.toFixed(1) }} 秒</p></div>
            <button type="button" class="preview-label" :disabled="!video" @click="preview">预览</button>
          </div>
          <button type="button" class="primary apply-clip" @click="save()">用这段并继续</button>
          <small v-if="handsfree" class="voice-confirmation">也可以说“TrainPal，确认”</small>
        </template>
        <template v-else>
          <p v-if="!textEntry" class="voice-help">{{ item.sourceRef ? '重看讲解、回退视频，或暂停训练。' : '可以说“暂停”或“继续训练”。' }}</p>
          <div class="voice-actions">
            <button type="button" class="primary" :disabled="training.commandLocked" @click="speakOnce">点一下说话</button>
            <button type="button" :aria-expanded="textEntry" @click="textEntry = !textEntry">{{ textEntry ? '收起文字' : '用文字' }}</button>
          </div>
        </template>
        <form v-if="textEntry && !voiceNotice && !locked && !listening" @submit.prevent="find()">
          <label for="clip-request">{{ item.sourceRef ? '想看哪段讲解？' : '想让我做什么？' }}</label>
          <div class="text-request"><input id="clip-request" v-model="instruction" maxlength="240" :placeholder="item.sourceRef ? '例如：重看呼吸讲解' : '例如：继续训练'" /><button type="submit" class="primary" :disabled="!instruction.trim() || training.commandLocked">发送</button></div>
        </form>
        <p v-if="message" class="request-message" role="status">{{ message }}</p>
        <p v-if="wakeStatus && phase === 'idle' && !listening" class="wake-status" role="status">{{ wakeStatus }}</p>
        <div v-if="!locked && !voiceNotice && !listening" class="voice-utilities">
          <button type="button" :aria-pressed="handsfree" @click="openWakeSettings">{{ handsfree ? '关闭语音唤醒' : '开启语音唤醒' }}</button>
          <details v-if="item.sourceRef">
            <summary>片段操作</summary><div class="clip-tools">
              <button type="button" :disabled="training.commandLocked" @click="shortcut('重看动作示范')">重看示范</button>
              <button type="button" :disabled="training.commandLocked" @click="shortcut('重看呼吸讲解')">呼吸要点</button>
              <button type="button" :disabled="!video" @click="rewind">回退 5 秒</button>
              <button v-if="item.playbackSelection" type="button" @click="save(true)">恢复完整教学</button>
            </div>
          </details>
        </div>
        <button v-if="!voiceNotice && phase !== 'saving'" type="button" class="cancel-request" @click="close">取消</button>
      </div>
    </dialog>
  </section>
</template>

<style scoped>
.playback-controls { color: var(--tp-ink); min-width: 0; }
.voice-entry-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 0 12px; }
.voice-shortcut { min-width: 0; }
.voice-entry { position: relative; display: flex; align-items: center; gap: 7px; padding: 8px 14px; border-radius: 28px; background: var(--tp-surface); box-shadow: 0 3px 16px rgb(21 46 67 / 8%); font-weight: 750; }
.voice-entry :deep(.trainpal-coach) { margin: -26px -2px -4px -7px; pointer-events: none; }
.voice-entry :deep(.trainpal-coach__image) { width: 56px; height: 64px; object-fit: contain; filter: none; }
.voice-entry svg { width: 20px; color: var(--tp-primary-readable); }
.voice-shortcut > small { display: block; margin-top: 5px; color: var(--tp-muted); font-size: 11px; }
.wake-switch { display: flex; align-items: center; gap: 5px; padding: 8px; font-size: 12px; white-space: nowrap; background: transparent; }
.wake-switch i { width: 6px; height: 6px; border-radius: 50%; background: var(--tp-muted); }
.wake-switch[aria-pressed="true"] { color: var(--tp-primary-readable); }
.wake-switch[aria-pressed="true"] i { background: currentColor; }
.entry-feedback { font-size: 12px; margin: 0 0 8px; color: var(--tp-muted); }
.voice-sheet { position: fixed; inset: auto 0 0; width: min(100%, 540px); max-width: 100%; max-height: min(72dvh, 640px); margin: auto auto 0; padding: 0; border: 1px solid var(--tp-line); border-bottom: 0; border-radius: 24px 24px 0 0; background: var(--tp-surface); color: var(--tp-ink); overscroll-behavior: contain; }
.voice-sheet::backdrop { background: rgb(21 46 67 / 18%); }
.voice-panel { padding: 10px 20px max(20px, env(safe-area-inset-bottom)); }
.sheet-handle { width: 38px; height: 4px; border-radius: 3px; background: var(--tp-line); margin: 0 auto 16px; }
.voice-heading { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.voice-heading :deep(.trainpal-coach) { margin: 0; }
.voice-heading :deep(.trainpal-coach__image) { width: 52px; height: 60px; object-fit: contain; filter: none; }
.voice-heading > div { flex: 1; }
.voice-heading small { color: var(--tp-muted); font-size: 12px; }
h2 { margin: 4px 0 0; font-size: 21px; }
button, summary { min-height: 44px; cursor: pointer; }
button { padding: 10px 14px; border: 0; border-radius: 12px; background: var(--tp-surface-raised); color: var(--tp-ink); font: inherit; font-size: 14px; transition: background 160ms, transform 160ms; }
button:active:not(:disabled) { transform: scale(.98); }
button:disabled { opacity: .5; cursor: default; }
button:focus-visible, input:focus-visible, summary:focus-visible { outline: 3px solid var(--tp-primary-readable); outline-offset: 3px; }
button.primary { color: var(--tp-ink); background: var(--tp-primary); font-weight: 750; }
.close-sheet { align-self: flex-start; width: 44px; padding: 10px; background: transparent; }
svg { width: 24px; height: 24px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.voice-help, .request-message, .wake-status, .voice-privacy p { font-size: 13px; line-height: 1.65; }
.voice-help, .wake-status { color: var(--tp-muted); }
.voice-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.voice-actions .primary { flex: 1; }
summary { display: flex; align-items: center; font-size: 12px; color: var(--tp-muted); }
.voice-privacy { margin-top: 8px; }
.voice-privacy p { margin: 0; color: var(--tp-muted); }
.listening-state { text-align: center; padding: 4px 0; }
.listening-state p { font-size: 14px; }
.voice-wave { display: flex; justify-content: center; align-items: center; gap: 5px; height: 54px; color: var(--tp-primary-readable); }
.voice-wave i { width: 4px; height: calc(7px + var(--bar) * 5px); background: currentColor; border-radius: 3px; animation: voice-pulse 900ms ease-in-out infinite alternate; animation-delay: calc(var(--bar) * -130ms); }
@keyframes voice-pulse { to { transform: scaleY(.45); } }
.finding-state { padding: 10px 0 16px; text-align: center; color: var(--tp-muted); }
.finding-dots { font-size: 40px; line-height: .7; color: var(--tp-primary-readable); }
.clip-proposal { display: flex; align-items: center; gap: 12px; padding: 12px; margin: 8px 0 16px; background: var(--tp-surface-raised); border-radius: 14px; }
.clip-proposal > div { flex: 1; min-width: 0; }
.clip-proposal strong { display: block; font-size: 15px; overflow-wrap: anywhere; }
.clip-proposal p { color: var(--tp-muted); font-size: 12px; margin: 6px 0 0; }
.preview-tile { width: 56px; min-height: 58px; background: var(--tp-ink); color: white; }
.preview-tile svg { fill: currentColor; stroke: none; }
.preview-label { padding: 8px; background: transparent; color: var(--tp-primary-readable); }
.apply-clip { width: 100%; min-height: 50px; }
.voice-confirmation { display: block; margin: 12px 0 0; text-align: center; color: var(--tp-muted); font-size: 12px; }
.cancel-request { display: block; width: 100%; margin-top: 10px; background: transparent; }
form { margin: 16px 0; }
label { font-size: 13px; }
.text-request { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
input { flex: 1; min-width: 0; min-height: 46px; padding: 10px; border: 1px solid var(--tp-line); border-radius: 10px; background: var(--tp-canvas); color: var(--tp-ink); font: inherit; font-size: 14px; }
.voice-utilities { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 10px; border-top: 1px solid var(--tp-line); margin-top: 16px; padding-top: 8px; }
.voice-utilities > button { background: transparent; font-size: 12px; color: var(--tp-muted); }
.clip-tools { display: flex; flex-wrap: wrap; gap: 6px; }
.clip-tools button { font-size: 12px; }
:global(body:has(.voice-sheet[open])) { overflow: hidden; }
@media (min-width: 768px) { .voice-sheet { inset: auto 24px 24px auto; margin: 0; border-bottom: 1px solid var(--tp-line); border-radius: 24px; } }
@media (prefers-reduced-motion: reduce) { .voice-wave i { animation: none; } button { transition: none; } }
</style>
