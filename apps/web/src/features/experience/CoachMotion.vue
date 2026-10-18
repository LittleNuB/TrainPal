<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  COACH_STYLE_LABELS,
  coachFrameMilliseconds,
  coachFrameUrls,
  isCoachActionForStyle,
  resolveCoachBaseAction,
  resolveCoachEventAction,
  type CoachAction,
  type CoachMotionCue,
  type CoachStyleId,
} from '@/domain/coach'

import type { PetState } from './pet-state'

const props = withDefaults(
  defineProps<{
    styleId: CoachStyleId | null
    state: PetState
    cue?: CoachMotionCue | null
    previewAction?: CoachAction | null
    visible?: boolean
  }>(),
  { cue: null, previewAction: null, visible: true },
)

const motionQuery = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-reduced-motion: reduce)')
  : null
const reducedMotion = ref(motionQuery?.matches ?? false)
const currentAction = ref<CoachAction>('idle')
const currentFrameIndex = ref(0)
const playingOnce = ref(false)
const assetFailed = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

const renderable = computed(() => props.visible && props.styleId !== null)
const frames = computed(() => props.styleId
  ? coachFrameUrls(props.styleId, currentAction.value)
  : [])
const currentFrameUrl = computed(() => frames.value[currentFrameIndex.value] ?? '')

const statusCopy = {
  idle: {
    alt: '在旁边等你开始',
    fallback: 'TrainPal 正在等你',
  },
  training: {
    alt: '正在陪你训练',
    fallback: 'TrainPal 正在陪你训练',
  },
  resting: {
    alt: '正在陪你休息',
    fallback: 'TrainPal 正在陪你休息',
  },
  paused: {
    alt: '正在等你继续',
    fallback: 'TrainPal 正在等你继续',
  },
  completed: {
    alt: '在庆祝训练完成',
    fallback: 'TrainPal 为你庆祝',
  },
} as const satisfies Record<PetState, { alt: string; fallback: string }>

const alt = computed(() => props.styleId
  ? `${COACH_STYLE_LABELS[props.styleId]} TrainPal 小猫教练${statusCopy[props.state].alt}`
  : '')
const fallback = computed(() => statusCopy[props.state].fallback)

function stopTimer(): void {
  if (timer !== null) clearTimeout(timer)
  timer = null
}

function scheduleNextFrame(): void {
  stopTimer()
  if (!renderable.value || reducedMotion.value || !props.styleId) return

  timer = setTimeout(() => {
    if (playingOnce.value && currentFrameIndex.value === frames.value.length - 1) {
      showBaseMotion()
      return
    }

    currentFrameIndex.value = playingOnce.value
      ? currentFrameIndex.value + 1
      : (currentFrameIndex.value + 1) % frames.value.length
    scheduleNextFrame()
  }, coachFrameMilliseconds(props.styleId, currentAction.value))
}

function showBaseMotion(): void {
  stopTimer()
  playingOnce.value = false
  currentFrameIndex.value = 0
  assetFailed.value = false
  if (!props.styleId) {
    currentAction.value = 'idle'
    return
  }
  currentAction.value = props.previewAction
    && isCoachActionForStyle(props.styleId, props.previewAction)
    ? props.previewAction
    : resolveCoachBaseAction(props.styleId, props.state)
  scheduleNextFrame()
}

function playCue(cue: CoachMotionCue | null): void {
  if (
    !cue
    || props.previewAction
    || !props.styleId
    || reducedMotion.value
    || !renderable.value
  ) {
    showBaseMotion()
    return
  }

  const action = resolveCoachEventAction(props.styleId, cue.event)
  if (!action) {
    showBaseMotion()
    return
  }

  stopTimer()
  playingOnce.value = true
  currentAction.value = action
  currentFrameIndex.value = 0
  assetFailed.value = false
  scheduleNextFrame()
}

watch(
  () => [
    props.styleId,
    props.state,
    props.visible,
    props.previewAction,
    reducedMotion.value,
  ] as const,
  showBaseMotion,
  { immediate: true },
)

watch(
  () => [props.cue?.sequence ?? null, props.cue?.event ?? null] as const,
  () => playCue(props.cue),
  { immediate: true },
)

const handleMotionPreference = (event: MediaQueryListEvent): void => {
  reducedMotion.value = event.matches
}
motionQuery?.addEventListener('change', handleMotionPreference)

onBeforeUnmount(() => {
  stopTimer()
  motionQuery?.removeEventListener('change', handleMotionPreference)
})
</script>

<template>
  <figure
    v-if="renderable"
    class="coach-motion trainpal-coach"
    :data-style="styleId"
    :data-state="state"
    :data-action="currentAction"
    :data-frame="currentFrameIndex + 1"
  >
    <img
      v-if="!assetFailed"
      class="coach-motion__image trainpal-coach__image"
      :src="currentFrameUrl"
      :alt="alt"
      width="112"
      height="126"
      @error="assetFailed = true"
    />
    <figcaption v-else class="coach-motion__fallback trainpal-coach__fallback">
      {{ fallback }}
    </figcaption>
  </figure>
</template>

<style scoped>
.coach-motion {
  display: grid;
  width: fit-content;
  margin: 0;
  place-items: center;
}

.coach-motion__image {
  display: block;
  width: clamp(88px, 28vw, 112px);
  height: auto;
  aspect-ratio: 8 / 9;
  object-fit: contain;
  filter: drop-shadow(0 10px 24px rgb(0 0 0 / 42%));
}

.coach-motion__fallback {
  max-width: 168px;
  color: #9eaaa7;
  font-size: 12px;
  line-height: 1.45;
  text-align: center;
}
</style>
