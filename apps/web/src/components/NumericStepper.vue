<script setup lang="ts">
import { computed, ref, useId } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: number | null
  label: string
  min?: number
  step?: number
  invalid?: boolean
  disabled?: boolean
  optional?: boolean
}>(), { min: 1, step: 1, invalid: false, disabled: false, optional: false })
const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()
const id = useId()
const touched = ref(false)
const invalidValue = computed(() => props.modelValue === null
  ? !props.optional
  : !Number.isFinite(props.modelValue) || props.modelValue < props.min
    || (props.step === 1 && !Number.isInteger(props.modelValue)))
const showError = computed(() => props.invalid || (touched.value && invalidValue.value))
const input = (event: Event) => {
  touched.value = true
  const raw = (event.target as HTMLInputElement).value
  emit('update:modelValue', raw.trim() === '' ? null : Number(raw))
}
const increment = (direction: number) => {
  touched.value = true
  const value = props.modelValue ?? props.min
  emit('update:modelValue', Math.max(props.min, Math.round((value + props.step * direction) * 100) / 100))
}
</script>

<template>
  <div class="numeric-field">
    <label :for="id">{{ label }}</label>
    <div class="numeric-stepper" :class="{ invalid: showError }">
      <button type="button" :aria-label="`减少${label}`" :disabled="disabled || (modelValue !== null && modelValue <= min)" @click="increment(-1)">−</button>
      <input :id="id" type="number" inputmode="decimal" :aria-label="label" :min="min" :step="step" :value="modelValue ?? ''" :disabled="disabled" :aria-invalid="showError || undefined" :aria-describedby="showError ? `${id}-error` : undefined" @input="input" />
      <button type="button" :aria-label="`增加${label}`" :disabled="disabled" @click="increment(1)">＋</button>
    </div>
    <small v-if="showError" :id="`${id}-error`" class="number-error">{{ step === 1 ? `请输入不小于 ${min} 的整数` : `请输入不小于 ${min} 的数字` }}</small>
  </div>
</template>

<style scoped>
.numeric-field { display: grid; min-width: 0; align-content: start; gap: 8px; }
label { color: var(--tp-muted); font-size: 14px; line-height: 1.5; }
.numeric-stepper { display: grid; grid-template-columns: 44px minmax(0, 1fr) 44px; gap: 4px; }
button, input { width: 100%; min-width: 0; min-height: 44px; border: 1px solid var(--tp-line); border-radius: 9px; color: var(--tp-ink); background: var(--tp-surface); text-align: center; }
button { font-size: 23px; background: var(--tp-surface-raised); }
button:disabled { opacity: .45; cursor: default; }
input { padding: 4px 2px; font: 650 20px/1.2 var(--font-display); font-variant-numeric: tabular-nums; appearance: textfield; }
input::-webkit-inner-spin-button, input::-webkit-outer-spin-button { appearance: none; margin: 0; }
.invalid input { border-color: var(--tp-danger); }
.number-error { color: var(--tp-danger); font-size: 12px; line-height: 1.5; }
</style>
