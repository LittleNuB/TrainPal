<script setup lang="ts">
import { ref, watch } from 'vue'

import type { GymtiQuestionViewModel } from './ui-types'

const props = withDefaults(defineProps<{
  question: GymtiQuestionViewModel
  busy: boolean
  failure: boolean
  answeredOptionId?: string | null
}>(), { answeredOptionId: null })

const emit = defineEmits<{
  select: [optionId: string]
  retry: []
  exit: []
}>()

const selectedOptionId = ref<string | null>(props.answeredOptionId)
const submitting = ref(false)

watch(
  () => [props.question.id, props.answeredOptionId] as const,
  () => {
    selectedOptionId.value = props.answeredOptionId
    submitting.value = false
  },
)

const select = (optionId: string): void => {
  if (props.busy || props.failure || submitting.value) return
  submitting.value = true
  selectedOptionId.value = optionId
  emit('select', optionId)
}
</script>

<template>
  <section class="question-step" :aria-busy="busy || submitting">
    <div class="question-copy">
      <p class="tp-kicker">训练偏好</p>
      <p class="question-helper">通常 5–8 题</p>
      <h1>{{ question.prompt }}</h1>
      <p v-if="question.context" class="question-context">{{ question.context }}</p>
    </div>

    <div class="option-list" role="radiogroup" :aria-label="question.prompt">
      <button
        v-for="(option, index) in question.options"
        :key="option.id"
        type="button"
        :data-option-id="option.id"
        :class="{ selected: selectedOptionId === option.id }"
        :aria-checked="selectedOptionId === option.id"
        :disabled="busy || failure || submitting"
        role="radio"
        @click="select(option.id)"
      >
        <span>{{ String.fromCharCode(65 + index) }}</span>
        <span>
          <strong>{{ option.label }}</strong>
          <small v-if="option.detail">{{ option.detail }}</small>
        </span>
        <i aria-hidden="true">{{ selectedOptionId === option.id ? '✓' : '→' }}</i>
      </button>
    </div>

    <div v-if="(busy || submitting) && !failure" class="selection-status" role="status">
      <i aria-hidden="true" />
      <span>TrainPal 正在挑下一题…</span>
    </div>

    <div v-if="failure" class="contract-failure" role="alert">
      <strong>这一步暂时没能完成</strong>
      <p>已经完成的回答仍保存在当前设备；可以重试，也可以先退出，稍后继续。</p>
      <div>
        <button data-action="exit" type="button" @click="emit('exit')">先退出</button>
        <button data-action="retry" type="button" @click="emit('retry')">重试这一步</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.question-step { display: grid; gap: 24px; padding: 18px 0 48px; }
.question-copy { display: grid; gap: 10px; }
.question-helper { width: fit-content; margin: 0; padding: 6px 9px; border-radius: 8px; color: var(--tp-secondary); background: var(--tp-sage-surface); font-size: 11px; font-weight: 600; }
.question-copy h1 { max-width: 680px; margin: 5px 0 0; font-size: clamp(24px, 6vw, 32px); line-height: 1.4; letter-spacing: -.025em; }
.question-context { margin: 0; color: var(--tp-muted); font-size: 13px; line-height: 1.65; }
.option-list { display: grid; gap: 10px; }
.option-list button { display: grid; grid-template-columns: 34px minmax(0, 1fr) 24px; width: 100%; min-height: 72px; align-items: center; gap: 11px; padding: 13px 13px 13px 12px; border: 1px solid var(--tp-line); border-radius: 14px; color: var(--tp-ink); background: var(--tp-surface); box-shadow: 0 5px 20px rgb(28 40 34 / 4%); text-align: left; }
.option-list button > span:first-child { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 8px; color: var(--tp-secondary); background: var(--tp-surface-raised); font: 800 16px/1 var(--font-display); }
.option-list strong,
.option-list small { display: block; }
.option-list strong { font-size: 14px; line-height: 1.55; }
.option-list small { margin-top: 4px; color: var(--tp-muted); font-size: 11px; line-height: 1.45; }
.option-list i { color: var(--tp-primary); font-size: 20px; font-style: normal; text-align: center; }
.option-list button.selected { border-color: var(--tp-secondary); background: var(--tp-surface-raised); }
.option-list button:disabled { opacity: 1; }
.option-list button:disabled:not(.selected) { color: #767A75; background: #FAF8F2; }
.selection-status { display: flex; min-height: 44px; align-items: center; gap: 10px; color: var(--tp-muted); font-size: 12px; font-weight: 700; }
.selection-status i { width: 9px; height: 9px; border-radius: 50%; background: var(--tp-secondary); box-shadow: 0 0 0 5px rgb(165 186 99 / 14%); animation: status-pulse 1s ease-in-out infinite alternate; }
.contract-failure { display: grid; gap: 7px; padding: 15px; border: 1px solid rgb(179 38 30 / 24%); border-radius: 16px; color: var(--tp-danger); background: rgb(179 38 30 / 5%); }
.contract-failure strong { font-size: 13px; }
.contract-failure p { margin: 0; color: var(--tp-muted); font-size: 11px; line-height: 1.6; }
.contract-failure > div { display: flex; justify-content: flex-end; gap: 8px; }
.contract-failure button { min-height: 44px; padding: 0 14px; border: 1px solid var(--tp-line); border-radius: 999px; color: var(--tp-ink); background: transparent; font-weight: 800; }
.contract-failure button:last-child { color: #FFFDF8; border-color: var(--tp-primary); background: var(--tp-primary-readable); }

@keyframes status-pulse {
  to { transform: scale(.72); opacity: .65; }
}

@media (prefers-reduced-motion: reduce) {
  .selection-status i { animation: none; }
}
</style>
