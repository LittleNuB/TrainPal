<script setup lang="ts">
import { computed, onMounted } from 'vue'

import { COACH_STYLE_LABELS, type CoachStyleId } from '@/domain/coach'
import type {
  CompletedGymtiResult,
  GymtiNarrativeSnapshot,
} from '@/domain/gymti'
import CoachMotion from '@/features/experience/CoachMotion.vue'
import {
  COACH_STYLE_PRESENTATION,
  GYMTI_TYPE_PRESENTATION,
  gymtiReasonText,
} from '@/features/gymti/presentation'

const props = defineProps<{
  current: {
    result: CompletedGymtiResult
    narrative: GymtiNarrativeSnapshot
  }
  confirmedStyleId: CoachStyleId | null
  busy: boolean
}>()

const emit = defineEmits<{
  confirm: []
  modify: []
  retest: []
  ready: []
}>()

onMounted(() => emit('ready'))

const formal = computed(() => props.current.result)
const gymti = computed(() => GYMTI_TYPE_PRESENTATION[formal.value.gymtiType])
const secondary = computed(() => formal.value.secondaryGymtiType
  ? GYMTI_TYPE_PRESENTATION[formal.value.secondaryGymtiType]
  : null)
const style = computed(() => COACH_STYLE_PRESENTATION[formal.value.recommendedCoachStyleId])
const alreadyConfirmed = computed(() =>
  props.confirmedStyleId === formal.value.recommendedCoachStyleId)
</script>

<template>
  <section class="result-step">
    <article class="gymti-result" data-result-section="gymti">
      <div class="gymti-art">
        <img
          :src="gymti.illustrationUrl"
          :alt="`${gymti.label} GYMTI 插画`"
          width="720"
          height="588"
        />
      </div>
      <div class="identity-copy">
        <p class="tp-kicker">你的 GYMTI</p>
        <h1>{{ gymti.label }}</h1>
        <p class="identity-summary">{{ gymti.shortDescription }}</p>
        <p class="narrative">{{ current.narrative.text }}</p>
      </div>

      <ul v-if="formal.reasonCodes.length" class="reason-list" aria-label="这次结果的主要依据">
        <li v-for="reasonCode in formal.reasonCodes.slice(0, 3)" :key="reasonCode">
          <i aria-hidden="true">✓</i>
          <span>{{ gymtiReasonText(reasonCode) }}</span>
        </li>
      </ul>
      <p v-if="secondary" class="secondary-tendency">
        次要倾向 · <strong>{{ secondary.label }}</strong>
      </p>
    </article>

    <article class="coach-result tp-card" data-result-section="coach">
      <div class="coach-copy">
        <p class="tp-kicker">你的 TrainPal</p>
        <span class="recommendation-badge">本次推荐</span>
        <h2>{{ COACH_STYLE_LABELS[formal.recommendedCoachStyleId] }}</h2>
        <p>{{ style.matchReason }}</p>
        <button data-action="modify" type="button" :disabled="busy" @click="emit('modify')">
          修改风格
        </button>
      </div>
      <div class="coach-art">
        <CoachMotion
          :style-id="formal.recommendedCoachStyleId"
          state="idle"
        />
      </div>
    </article>

    <button data-action="retest" class="retest" type="button" :disabled="busy" @click="emit('retest')">
      重新测评
    </button>

    <footer class="result-action">
      <button
        data-primary-action
        data-action="confirm"
        type="button"
        :disabled="busy"
        @click="emit('confirm')"
      >
        {{ busy ? '正在保存…' : alreadyConfirmed ? '继续使用这个风格' : '确认这个风格' }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.result-step { display: grid; gap: 20px; padding: 12px 0 112px; }
.gymti-result { display: grid; gap: 17px; }
.gymti-art { width: 100%; max-width: 600px; justify-self: center; overflow: hidden; border: 1px solid var(--tp-line); border-radius: 14px; background: #E9E1D1; box-shadow: var(--tp-shadow-soft); }
.gymti-art img { display: block; width: 100%; height: auto; aspect-ratio: 60 / 49; object-fit: cover; }
.identity-copy { display: grid; gap: 9px; }
.identity-copy h1 { margin: 0; font-size: clamp(28px, 7vw, 40px); line-height: 1.3; letter-spacing: -.035em; }
.identity-summary { margin: 0; color: var(--tp-primary-readable); font-size: 14px; font-weight: 800; line-height: 1.55; }
.narrative { margin: 4px 0 0; color: var(--tp-ink); font-size: 15px; line-height: 1.75; }
.reason-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.reason-list li { display: flex; gap: 9px; align-items: start; padding: 12px 13px; border: 1px solid var(--tp-line); border-radius: 14px; color: var(--tp-muted); background: #F8F4EB; font-size: 12px; line-height: 1.55; }
.reason-list i { display: grid; width: 20px; height: 20px; flex: 0 0 auto; place-items: center; border-radius: 50%; color: #FFFDF8; background: var(--tp-secondary); font-size: 11px; font-style: normal; }
.secondary-tendency { width: fit-content; margin: 0; padding: 7px 10px; border-radius: 999px; color: var(--tp-muted); background: #EAE6DC; font-size: 11px; }
.secondary-tendency strong { color: var(--tp-ink); }
.coach-result { display: grid; grid-template-columns: minmax(0, 1fr) 118px; min-height: 214px; align-items: center; gap: 6px; overflow: hidden; padding: 18px 10px 18px 18px; background: var(--tp-training-surface); }
.coach-copy { display: grid; justify-items: start; gap: 7px; }
.coach-copy .tp-kicker { color: var(--tp-secondary); }
.recommendation-badge { padding: 5px 7px; border-radius: 999px; color: var(--tp-training-ink); background: rgb(165 186 99 / 18%); font-size: 11px; font-weight: 800; }
.coach-copy h2 { margin: 2px 0 0; color: var(--tp-training-ink); font-size: 25px; }
.coach-copy > p:not(.tp-kicker) { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.coach-copy button { min-height: 44px; padding: 0; border: 0; color: var(--tp-secondary); background: transparent; font-size: 12px; font-weight: 800; text-decoration: underline; text-underline-offset: 4px; }
.coach-art { display: grid; place-items: center; }
.coach-art :deep(.coach-motion__image) { width: 112px; filter: drop-shadow(0 10px 24px rgb(0 0 0 / 38%)); }
.retest { justify-self: center; min-height: 44px; padding: 0 12px; border: 0; color: var(--tp-muted); background: transparent; font-size: 12px; text-decoration: underline; text-underline-offset: 4px; }
.result-action { position: fixed; right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); left: max(14px, env(safe-area-inset-left)); z-index: 20; max-width: 732px; margin: auto; padding: 10px; border: 1px solid rgb(28 40 34 / 12%); border-radius: 20px; background: var(--tp-surface); box-shadow: var(--tp-shadow-float); }
.result-action button { width: 100%; min-height: 50px; border: 1px solid var(--tp-primary); border-radius: 999px; color: #FFFDF8; background: var(--tp-primary-readable); font-weight: 800; }
@media (min-width: 1024px) { .result-action { left: calc(var(--tp-sidebar-width, 216px) + 24px); } }
</style>
