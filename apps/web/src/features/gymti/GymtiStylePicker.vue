<script setup lang="ts">
import { ref, watch } from 'vue'

import {
  COACH_STYLE_IDS,
  COACH_STYLE_LABELS,
  coachFrameUrls,
  type CoachStyleId,
} from '@/domain/coach'
import CoachMotion from '@/features/experience/CoachMotion.vue'
import { COACH_STYLE_PRESENTATION } from '@/features/gymti/presentation'

const props = defineProps<{
  recommendedStyleId: CoachStyleId
  confirmedStyleId: CoachStyleId | null
  busy: boolean
}>()

const emit = defineEmits<{
  confirm: [styleId: CoachStyleId]
}>()

const selectedStyleId = ref<CoachStyleId>(props.recommendedStyleId)

watch(
  () => props.recommendedStyleId,
  (styleId) => { selectedStyleId.value = styleId },
)

</script>

<template>
  <section class="style-picker">
    <div class="style-intro">
      <p class="tp-kicker">小猫教练</p>
      <h1 class="tp-title">哪种陪练方式<br />更像你需要的？</h1>
      <p>点一只小猫看看，选好后确认即可。</p>
    </div>

    <div class="style-grid" role="group" aria-label="七种小猫教练风格">
      <button
        v-for="styleId in COACH_STYLE_IDS"
        :key="styleId"
        type="button"
        :data-style-id="styleId"
        :aria-pressed="selectedStyleId === styleId"
        :class="{ selected: selectedStyleId === styleId }"
        :disabled="busy"
        @click="selectedStyleId = styleId"
      >
        <div class="style-visual">
          <CoachMotion
            v-if="selectedStyleId === styleId"
            :style-id="styleId"
            state="idle"
          />
          <img
            v-else
            :src="coachFrameUrls(styleId, 'idle')[0]"
            :alt="`${COACH_STYLE_LABELS[styleId]}小猫教练`"
            width="112"
            height="126"
          />
        </div>
        <div class="style-copy">
          <span class="badges">
            <small v-if="styleId === recommendedStyleId">本次推荐</small>
            <small v-if="styleId === confirmedStyleId" class="current">当前使用</small>
          </span>
          <strong>{{ COACH_STYLE_LABELS[styleId] }}</strong>
          <p>{{ COACH_STYLE_PRESENTATION[styleId].personality }}</p>
        </div>
        <i aria-hidden="true">{{ selectedStyleId === styleId ? '✓' : '' }}</i>
      </button>
    </div>

    <footer class="style-action">
      <button
        data-action="confirm"
        type="button"
        :disabled="busy"
        @click="emit('confirm', selectedStyleId)"
      >
        {{ busy ? '正在保存…' : `确认使用${COACH_STYLE_LABELS[selectedStyleId]}` }}
      </button>
    </footer>
  </section>
</template>

<style scoped>
.style-picker { display: grid; gap: 22px; padding: 18px 0 112px; }
.style-intro { display: grid; gap: 11px; }
.style-intro h1,
.style-intro p { margin: 0; }
.style-intro > p:last-child { max-width: 560px; color: var(--tp-muted); font-size: 13px; line-height: 1.65; }
.style-grid { display: grid; grid-template-columns: 1fr; gap: 11px; }
.style-grid > button { position: relative; display: grid; grid-template-columns: 96px minmax(0, 1fr) 22px; min-height: 138px; align-items: center; gap: 10px; overflow: hidden; padding: 12px; border: 1px solid var(--tp-line); border-radius: 14px; color: var(--tp-ink); background: var(--tp-surface); box-shadow: var(--tp-shadow-soft); text-align: left; }
.style-grid > button.selected { border-color: var(--tp-secondary); background: var(--tp-surface-raised); box-shadow: none; }
.style-visual { display: grid; width: 96px; height: 108px; place-items: center; overflow: hidden; border-radius: 12px; background: rgb(165 186 99 / 14%); }
.style-visual img,
.style-visual :deep(.coach-motion__image) { width: 86px; max-height: 104px; object-fit: contain; filter: drop-shadow(0 7px 12px rgb(28 40 34 / 18%)); }
.style-copy { min-width: 0; }
.badges { display: flex; min-height: 20px; flex-wrap: wrap; gap: 5px; }
.badges small { padding: 4px 6px; border-radius: 999px; color: var(--tp-primary-readable); background: rgb(217 75 43 / 10%); font-size: 11px; font-weight: 800; }
.badges small.current { color: #46602E; background: rgb(165 186 99 / 18%); }
.style-copy strong { display: block; margin-top: 7px; font-size: 17px; }
.style-copy p { margin: 5px 0 0; color: var(--tp-muted); font-size: 11px; line-height: 1.55; }
.style-grid > button > i { display: grid; width: 22px; height: 22px; place-items: center; border: 1px solid var(--tp-line); border-radius: 50%; color: #FFFDF8; background: transparent; font-size: 12px; font-style: normal; }
.style-grid > button.selected > i { border-color: var(--tp-primary); background: var(--tp-primary); }
.style-action { position: fixed; right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); left: max(14px, env(safe-area-inset-left)); z-index: 20; max-width: 732px; margin: auto; padding: 10px; border: 1px solid rgb(28 40 34 / 12%); border-radius: 14px; background: var(--tp-surface); box-shadow: var(--tp-shadow-float); }
.style-action button { width: 100%; min-height: 50px; border: 1px solid var(--tp-primary); border-radius: 999px; color: #FFFDF8; background: var(--tp-primary-readable); font-weight: 800; }

@media (min-width: 360px) {
  .style-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .style-grid > button { grid-template-columns: 1fr 22px; min-height: 280px; align-content: start; }
  .style-visual { grid-column: 1 / -1; width: 100%; height: 136px; }
}

@media (min-width: 700px) {
  .style-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (min-width: 1024px) { .style-action { left: calc(var(--tp-sidebar-width, 216px) + 24px); } }
</style>
