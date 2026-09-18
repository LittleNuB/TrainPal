<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { placeCoach, type CoachPoint, type CoachRect } from '@/domain/coach-position'
import { useLibraryStore } from '@/stores/library'
import CoachMotion from './CoachMotion.vue'

const props = defineProps<{ suspended?: boolean }>()
const emit = defineEmits<{ adjust: [event: Event] }>()
const library = useLibraryStore()
const root = ref<HTMLElement | null>(null)
const point = ref<CoachPoint>({ x: 8, y: 120 })
const placed = ref(false)
const forcedCompact = ref(false)
const moving = ref(false)
const saving = ref(false)
const error = ref('')
const compact = computed(() => !library.preferences.petVisible || forcedCompact.value)
const layout = () => window.innerWidth < 1024 ? 'mobile' as const : 'desktop' as const
const size = () => compact.value ? { width: 48, height: 48 } : { width: 112, height: 132 }
let drag: { pointerId: number; origin: CoachPoint; pointer: CoachPoint } | null = null
let observer: ResizeObserver | null = null
let frame = 0
const obstacleSelector = '[data-coach-avoid], .global-task-dock, .top-level-nav, .plan-page button, .plan-page a, .plan-page input, .plan-page select, .plan-page textarea'

function viewport(): CoachRect {
  const visual = window.visualViewport
  return { x: visual?.offsetLeft ?? 0, y: visual?.offsetTop ?? 0, width: visual?.width ?? window.innerWidth, height: visual?.height ?? window.innerHeight }
}

function obstacles(): CoachRect[] {
  return [...document.querySelectorAll<HTMLElement>(obstacleSelector)]
    .filter((element) => !element.closest('.floating-coach') && element.getClientRects().length)
    .map((element) => {
      const rect = element.getBoundingClientRect()
      return { x: rect.x - 4, y: rect.y - 4, width: rect.width + 8, height: rect.height + 8 }
    })
}

function settle(desired = point.value, snap = true): void {
  if (props.suspended) return
  const next = placeCoach(desired, viewport(), size(), obstacles(), snap && layout() === 'mobile')
  if (next) {
    point.value = next
    placed.value = true
  } else if (!compact.value) {
    forcedCompact.value = true
    settle(desired, snap)
  } else {
    placed.value = false
  }
}

function restore(): void {
  forcedCompact.value = false
  const bounds = viewport()
  const saved = library.preferences.coachPositions?.[layout()]
  const valid = saved && [saved.x, saved.y].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)
  settle(valid ? {
    x: bounds.x + saved.x * Math.max(0, bounds.width - size().width),
    y: bounds.y + saved.y * Math.max(0, bounds.height - size().height),
  } : { x: bounds.x + bounds.width - size().width - 16, y: bounds.y + bounds.height * .56 })
}

function schedulePlacement(): void {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => {
    if (!drag) restore()
  })
}

async function persistPosition(): Promise<void> {
  if (saving.value) return
  saving.value = true
  error.value = ''
  const bounds = viewport()
  const clamp = (value: number) => Math.max(0, Math.min(1, value))
  try {
    await library.setCoachPosition(layout(), {
      x: clamp((point.value.x - bounds.x) / Math.max(1, bounds.width - size().width)),
      y: clamp((point.value.y - bounds.y) / Math.max(1, bounds.height - size().height)),
    })
  } catch {
    error.value = '位置未保存，可再次移动重试'
  } finally {
    saving.value = false
  }
}

function pointerDown(event: PointerEvent): void {
  if (!event.isPrimary || event.button !== 0 || saving.value) return
  drag = { pointerId: event.pointerId, origin: { ...point.value }, pointer: { x: event.clientX, y: event.clientY } }
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}

function pointerMove(event: PointerEvent): void {
  if (!drag || drag.pointerId !== event.pointerId) return
  const dx = event.clientX - drag.pointer.x
  const dy = event.clientY - drag.pointer.y
  if (Math.hypot(dx, dy) > 4) moving.value = true
  if (moving.value) settle({ x: drag.origin.x + dx, y: drag.origin.y + dy }, false)
}

function pointerEnd(event: PointerEvent): void {
  if (!drag || drag.pointerId !== event.pointerId) return
  const moved = moving.value
  const origin = drag.origin
  drag = null
  moving.value = false
  settle(event.type === 'pointercancel' ? origin : point.value)
  if (moved && event.type !== 'pointercancel') void persistPosition()
}

function moveByKey(event: KeyboardEvent): void {
  const delta: Record<string, CoachPoint> = {
    ArrowLeft: { x: -24, y: 0 }, ArrowRight: { x: 24, y: 0 },
    ArrowUp: { x: 0, y: -24 }, ArrowDown: { x: 0, y: 24 },
  }
  const step = delta[event.key]
  if (!step) return
  event.preventDefault()
  if (saving.value) return
  const mobileX = layout() === 'mobile' && step.x
    ? (step.x < 0 ? 0 : window.innerWidth) : point.value.x + step.x
  settle({ x: mobileX, y: point.value.y + step.y })
  void persistPosition()
}

async function toggle(): Promise<void> {
  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    await library.setPetVisible(!library.preferences.petVisible || forcedCompact.value)
    forcedCompact.value = false
    await nextTick()
    restore()
    root.value?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true })
  } catch {
    error.value = '显示偏好未保存，请重试'
  } finally {
    saving.value = false
  }
}

watch(() => props.suspended, (suspended) => { if (!suspended) schedulePlacement() })
onMounted(() => {
  restore()
  window.addEventListener('resize', schedulePlacement)
  window.addEventListener('scroll', schedulePlacement, { passive: true })
  window.visualViewport?.addEventListener('resize', schedulePlacement)
  window.visualViewport?.addEventListener('scroll', schedulePlacement)
  observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedulePlacement)
  document.querySelectorAll<HTMLElement>(`${obstacleSelector}, .plan-page`).forEach((element) => {
    if (!element.closest('.floating-coach')) observer?.observe(element)
  })
})
onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  observer?.disconnect()
  window.removeEventListener('resize', schedulePlacement)
  window.removeEventListener('scroll', schedulePlacement)
  window.visualViewport?.removeEventListener('resize', schedulePlacement)
  window.visualViewport?.removeEventListener('scroll', schedulePlacement)
})
</script>

<template>
  <aside
    v-if="library.preferences.coachStyleId"
    v-show="placed && !suspended"
    ref="root"
    class="floating-coach"
    :class="{ 'is-moving': moving, 'is-compact': compact }"
    :style="{ transform: `translate3d(${point.x}px, ${point.y}px, 0)` }"
    aria-label="TrainPal 小猫教练"
  >
    <button v-if="compact" type="button" class="restore-coach" aria-label="恢复小猫教练" :disabled="saving" @click="toggle">猫咪</button>
    <template v-else>
      <button
        type="button"
        class="coach-drag-handle"
        aria-label="移动小猫教练，使用方向键或拖动"
        title="拖动可移动，方向键也可以"
        @pointerdown="pointerDown"
        @pointermove="pointerMove"
        @pointerup="pointerEnd"
        @pointercancel="pointerEnd"
        @lostpointercapture="pointerEnd"
        @keydown="moveByKey"
      >
        <CoachMotion :style-id="library.preferences.coachStyleId" state="idle" />
      </button>
      <div class="coach-controls">
        <button type="button" aria-label="让小猫调整这次训练" @click="emit('adjust', $event)">调整</button>
        <button type="button" aria-label="收起小猫教练" :disabled="saving" @click="toggle">收起</button>
      </div>
    </template>
    <p v-if="error" class="coach-save-error" role="status">{{ error }}</p>
  </aside>
</template>

<style scoped>
.floating-coach { position: fixed; top: 0; left: 0; z-index: 40; width: 112px; height: 132px; transition: transform 180ms ease; }
.floating-coach.is-moving { transition: none; }
.floating-coach.is-compact { width: 48px; height: 48px; }
.coach-drag-handle { display: grid; width: 112px; height: 88px; place-items: center; padding: 0; border: 0; border-radius: 12px; background: transparent; cursor: grab; touch-action: none; user-select: none; }
.is-moving .coach-drag-handle { cursor: grabbing; }
.coach-drag-handle :deep(figure) { margin: 0; pointer-events: none; }
.coach-drag-handle :deep(img) { width: 86px; height: 86px; object-fit: contain; filter: drop-shadow(0 5px 6px rgb(28 40 34 / 14%)); }
.coach-controls { display: flex; border: 1px solid var(--tp-line); border-radius: 8px; background: var(--tp-surface); }
.coach-controls button { flex: 1; min-width: 44px; min-height: 44px; padding: 0; border: 0; color: var(--tp-muted); background: transparent; font-size: 11px; }
.coach-controls button:hover { color: var(--tp-ink); background: var(--tp-canvas); }
.restore-coach { width: 48px; min-height: 48px; border: 1px solid var(--tp-line); border-radius: 12px; color: var(--tp-ink); background: var(--tp-surface); font-size: 11px; }
.coach-save-error { position: absolute; bottom: 100%; right: 0; width: 112px; margin: 0 0 6px; padding: 8px; border-radius: 8px; color: var(--tp-danger); background: var(--tp-surface); font-size: 11px; }
</style>
