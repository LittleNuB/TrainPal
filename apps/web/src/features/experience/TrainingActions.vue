<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { TrainingSession } from '@/domain/training'

const props = defineProps<{
  status: TrainingSession['status']
  mode: 'reps' | 'duration'
  startLabel: string
  disabled: boolean
  expanded?: boolean
}>()
defineEmits<{ pause: []; start: []; continue: []; complete: [] }>()
const controls = ref<HTMLElement | null>(null)
let restoreFocus = false
watch([() => props.status, () => props.disabled], async () => {
  restoreFocus ||= Boolean(controls.value?.contains(document.activeElement))
  await nextTick()
  if (props.disabled) return
  if (restoreFocus && document.activeElement === document.body) {
    controls.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
  }
  restoreFocus = false
}, { flush: 'sync' })
</script>

<template>
  <div ref="controls" class="training-controls" :class="{ 'training-controls--expanded': expanded }">
    <button v-if="status === 'countdown'" type="button" class="primary-action" :disabled="disabled" @click="$emit('pause')">先暂停</button>
    <button v-else-if="status === 'paused' || status === 'ready_to_continue'" type="button" class="primary-action" :disabled="disabled" @click="$emit('start')">{{ startLabel }}</button>
    <button v-else-if="status === 'resting'" type="button" class="primary-action rest-action" :disabled="disabled" @click="$emit('continue')">提前继续</button>
    <button v-else-if="mode === 'reps'" type="button" class="primary-action" :disabled="disabled" @click="$emit('complete')">完成本组</button>
    <p v-else class="active-duration-note" role="status">本组计时结束后自动完成</p>
    <button v-if="status === 'active' || status === 'resting'" type="button" class="pause-training" :disabled="disabled" @click="$emit('pause')">暂停</button>
  </div>
</template>

<style scoped>
.training-controls { display: grid; gap: 8px; }
.training-controls:has(.pause-training) { grid-template-columns: minmax(0, 1fr) auto; }
.primary-action { display: grid; width: 100%; min-height: 52px; place-items: center; border: 1px solid var(--tp-primary); border-radius: 12px; color: var(--tp-on-primary); background: var(--tp-primary); font-weight: 800; }
.pause-training { min-width: 80px; min-height: 52px; padding: 0 16px; border: 1px solid var(--tp-secondary); border-radius: 12px; color: var(--tp-training-ink); background: transparent; font-weight: 700; }
button:disabled { opacity: .5; }
button:focus-visible { outline: 2px solid var(--tp-focus); outline-offset: 3px; }
.active-duration-note { align-self: center; margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.training-controls--expanded .pause-training { color: white; border-color: #9bb4c8; }
.training-controls--expanded .active-duration-note { color: #bed0de; }
</style>
