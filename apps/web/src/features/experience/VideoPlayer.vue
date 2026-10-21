<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref, watch } from 'vue'
import { useDialogFocus } from '@/composables/useDialogFocus'

const props = withDefaults(defineProps<{
  src: string
  label: string
  range?: { start_seconds: number; end_seconds: number } | null
  muted?: boolean
  training?: boolean
}>(), { range: null, muted: false, training: false })
const emit = defineEmits<{
  'update:element': [element: HTMLVideoElement | null]
  loadedmetadata: []
  timeupdate: []
  ended: []
  error: []
  'user-playback': [playing: boolean]
  expanded: [value: boolean]
  'layout-change': []
}>()
const video = ref<HTMLVideoElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const expandButton = ref<HTMLButtonElement | null>(null)
const focus = useDialogFocus(panel)
const expanded = ref(false)
const expanding = ref(false)
const ratio = ref(16 / 9)
const duration = ref(0)
const position = ref(0)
const playing = ref(false)
const silenced = ref(props.muted)
const failed = ref(false)
const message = ref('')
let disposed = false
let previousOverflow = ''
let inertSiblings: Array<{ element: HTMLElement; inert: boolean }> = []
const portrait = computed(() => ratio.value < 1)
const start = computed(() => props.range?.start_seconds ?? 0)
const end = computed(() => Math.max(start.value, Math.min(props.range?.end_seconds ?? duration.value, duration.value)))
const elapsed = computed(() => Math.max(0, Math.min(position.value, end.value) - start.value))
const length = computed(() => Math.max(0, end.value - start.value))
const formatTime = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
const readMetadata = () => {
  const media = video.value
  if (!media) return
  if (media.videoWidth > 0 && media.videoHeight > 0) ratio.value = media.videoWidth / media.videoHeight
  duration.value = Number.isFinite(media.duration) ? media.duration : 0
  failed.value = false
}
const metadata = () => { readMetadata(); emit('loadedmetadata') }
const updateTime = () => { position.value = video.value?.currentTime ?? 0; emit('timeupdate') }
const togglePlayback = () => {
  const media = video.value
  if (!media) return
  message.value = ''
  if (!media.paused) {
    emit('user-playback', false)
    media.pause()
  } else {
    if (media.currentTime < start.value || media.currentTime >= end.value) media.currentTime = start.value
    emit('user-playback', true)
    void media.play().catch(() => { message.value = '没能播放，请再试一次。' })
  }
}
const seek = (event: Event) => {
  if (!video.value || !length.value) return
  video.value.currentTime = start.value + Math.max(0, Math.min(length.value, Number((event.target as HTMLInputElement).value)))
  updateTime()
}
const replay = () => {
  if (!video.value) return
  video.value.currentTime = start.value
  updateTime()
  emit('user-playback', true)
  void video.value.play().catch(() => { message.value = '没能播放，请再试一次。' })
}
const toggleMute = () => {
  if (!video.value) return
  video.value.muted = !video.value.muted
  silenced.value = video.value.muted
}
const close = () => {
  if (!expanded.value) return
  if (!props.training) video.value?.pause()
  expanded.value = false
  emit('expanded', false)
  if (document.body.style.overflow === 'hidden') document.body.style.overflow = previousOverflow
  inertSiblings.forEach(({ element, inert }) => { element.inert = inert })
  inertSiblings = []
  void focus.deactivate()
  if (!disposed && props.training) void nextTick(() => emit('layout-change'))
}
const expand = async () => {
  if (expanded.value || expanding.value) return
  expanding.value = true
  try {
    if (disposed) return
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    expanded.value = true
    emit('expanded', true)
    await nextTick()
    if (disposed) return
    emit('layout-change')
    inertSiblings = [...document.body.children]
      .filter((element): element is HTMLElement => element instanceof HTMLElement && !element.contains(panel.value))
      .map(element => ({ element, inert: element.inert }))
    inertSiblings.forEach(({ element }) => { element.inert = true })
    await focus.activate(expandButton.value)
  } finally { expanding.value = false }
}
watch(() => props.src, () => {
  video.value?.pause()
  ratio.value = 16 / 9
  duration.value = 0
  position.value = 0
  playing.value = false
  failed.value = false
  message.value = ''
})
watch(() => props.muted, value => { if (video.value) video.value.muted = value; silenced.value = value })
onMounted(() => { emit('update:element', video.value); readMetadata() })
onUpdated(async () => {
  await nextTick()
  if (expanded.value && !panel.value?.querySelector('[aria-busy="true"]') && document.activeElement === document.body) {
    panel.value?.querySelector<HTMLButtonElement>('[data-dialog-initial-focus]')?.focus()
  }
})
onBeforeUnmount(() => {
  disposed = true
  close()
  video.value?.pause()
  emit('update:element', null)
})
</script>

<template>
  <div class="video-player" :class="{ 'video-player--portrait': portrait, 'video-player--training': training }" :style="{ '--video-ratio': ratio }">
    <Teleport to="body" :disabled="!expanded">
      <section ref="panel" class="player-panel" :class="{ 'player-panel--expanded': expanded }" :role="expanded ? 'dialog' : 'group'" :aria-modal="expanded ? true : undefined" :aria-label="expanded ? `${label} · 放大观看` : '视频播放器'" @keydown="expanded && focus.onKeydown($event, close)">
        <header v-if="expanded" class="expanded-header">
          <span><slot name="expanded-title">{{ label }}</slot></span>
          <button type="button" data-dialog-initial-focus @click="close">收起视频 <span aria-hidden="true">×</span></button>
        </header>
        <div class="video-canvas" :class="{ 'video-canvas--portrait': portrait }" :style="{ '--video-ratio': ratio }">
          <video
            ref="video" :src="src" :aria-label="label" :muted="muted" playsinline preload="metadata"
            @loadedmetadata="metadata" @resize="readMetadata" @durationchange="readMetadata" @timeupdate="updateTime"
            @play="playing = true" @pause="playing = false" @ended="playing = false; emit('ended')"
            @volumechange="silenced = video?.muted ?? muted" @error="failed = true; emit('error')"
          />
          <p v-if="failed" class="player-error" role="status">视频暂时无法播放</p>
        </div>
        <div class="player-controls">
          <div class="player-timeline">
            <input type="range" :aria-label="range ? '当前动作片段进度' : '视频播放进度'" :aria-valuetext="`${formatTime(elapsed)} / ${formatTime(length)}`" min="0" :max="length || 1" step="0.1" :value="elapsed" :disabled="!length || failed" @input="seek">
            <span>{{ formatTime(elapsed) }} / {{ formatTime(length) }}</span>
          </div>
          <div class="player-actions">
            <button type="button" class="play-toggle" :disabled="failed" :aria-label="playing ? '暂停视频' : '播放视频'" @click="togglePlayback">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path v-if="playing" d="M7 5v14M17 5v14" /><path v-else d="m8 5 10 7-10 7Z" /></svg>
              {{ playing ? '暂停' : '播放' }}{{ training ? '示范' : '' }}
            </button>
            <button v-if="training" type="button" :disabled="!length || failed" @click="replay">重看本段</button>
            <button type="button" class="sound-toggle" :aria-label="silenced ? '打开视频声音' : '关闭视频声音'" :aria-pressed="silenced" @click="toggleMute">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M11 4 6 8H3v8h3l5 4Z" /><path v-if="silenced" d="m16 9 6 6m0-6-6 6" /><path v-else d="M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></svg>
            </button>
            <button v-show="!expanded" ref="expandButton" type="button" class="expand-video" :aria-hidden="expanded ? true : undefined" :disabled="expanding" aria-haspopup="dialog" @click="expand">
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6" /></svg><span>放大</span>
            </button>
          </div>
          <p v-if="message" role="status">{{ message }}</p>
        </div>
        <div v-if="expanded && $slots['expanded-controls']" class="expanded-training"><slot name="expanded-controls" /></div>
      </section>
    </Teleport>
  </div>
</template>

<style scoped>
.video-player { --player-height: min(48dvh, 480px); width: 100%; min-width: 0; }
.video-player--training { --player-height: clamp(180px, calc(100dvh - 520px), 520px); }
.player-panel { overflow: hidden; color: white; background: #102334; border-radius: 12px; }
.video-canvas { display: grid; place-items: center; position: relative; width: 100%; aspect-ratio: var(--video-ratio); max-height: var(--player-height, min(48dvh, 480px)); background: #0b1926; }
.video-canvas--portrait { height: var(--player-height, min(48dvh, 480px)); aspect-ratio: auto; }
.video-canvas video { display: block; width: 100%; height: 100%; min-height: 0; object-fit: contain; }
.player-controls { padding: 4px 12px 6px; }
.player-timeline { display: flex; align-items: center; gap: 12px; min-height: 28px; }
.player-timeline input { width: 100%; min-width: 0; height: 32px; margin: 0; accent-color: #19cdc0; cursor: pointer; }
.player-timeline span { flex-shrink: 0; color: #bed0de; font-size: 11px; font-variant-numeric: tabular-nums; }
.player-actions { display: flex; align-items: center; gap: 4px; }
.player-panel button { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-width: 44px; min-height: 44px; padding: 0 6px; border: 0; border-radius: 8px; color: white; background: transparent; font-size: 12px; white-space: nowrap; cursor: pointer; }
.player-panel button:hover { background: #243e51; }
.player-panel button:disabled { opacity: .45; cursor: default; }
.player-panel button:focus-visible, .player-panel input:focus-visible { outline: 2px solid #19cdc0; outline-offset: -2px; }
.player-panel svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.play-toggle { font-weight: 650; }
.sound-toggle { margin-left: auto; }
.player-controls p { margin: 4px 0; font-size: 12px; }
.player-error { position: absolute; padding: 10px; background: #102334; }
.player-panel--expanded { position: fixed; inset: 0; z-index: 100; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; width: 100%; height: 100dvh; padding: env(safe-area-inset-top) max(12px, env(safe-area-inset-right)) env(safe-area-inset-bottom) max(12px, env(safe-area-inset-left)); border-radius: 0; }
.player-panel--expanded .video-canvas { height: 100%; max-height: none; aspect-ratio: auto; }
.expanded-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 56px; font-size: 13px; }
.expanded-header button span { font-size: 24px; }
.player-panel--expanded .player-controls { width: min(100%, 900px); justify-self: center; }
.player-panel--expanded:has(.expanded-training) { grid-template-rows: auto minmax(0, 1fr) auto auto; }
.expanded-training { width: min(100%, 900px); justify-self: center; padding: 10px 0 max(12px, env(safe-area-inset-bottom)); border-top: 1px solid #375064; }
@media (min-width: 800px) { .video-player { --player-height: min(56dvh, 560px); } .video-player--training { --player-height: min(64dvh, 640px); } }
</style>
