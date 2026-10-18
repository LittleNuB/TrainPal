<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { SourceTip } from '@/domain/types'

const props = defineProps<{
  tips: SourceTip[]
  activeTip?: SourceTip | null
  review?: { enabled: boolean; approvedIndexes: number[] }
}>()
const emit = defineEmits<{ review: [indexes: number[]] }>()
const index = ref(0)
const current = computed(() => props.tips[index.value] ?? props.tips[0])
watch(() => JSON.stringify(props.tips), () => { index.value = 0 })
watch(() => props.activeTip, (tip) => {
  if (!tip) return
  const selected = props.tips.findIndex(entry => JSON.stringify(entry) === JSON.stringify(tip))
  if (selected >= 0) index.value = selected
}, { immediate: true })
const time = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
const reviewTip = (tipIndex: number, event: Event): void => {
  if (!props.review?.enabled) return
  const indexes = new Set(props.review.approvedIndexes)
  if ((event.target as HTMLInputElement).checked) indexes.add(tipIndex)
  else indexes.delete(tipIndex)
  emit('review', [...indexes].sort((a, b) => a - b))
}
</script>

<template>
  <aside v-if="current" class="source-tips" aria-label="视频动作要点">
    <div class="source-tips__heading"><span>来自视频 · 动作要点</span><button v-if="tips.length > 1" type="button" @click="index = (index + 1) % tips.length">下一条 {{ index + 1 }}/{{ tips.length }}</button></div>
    <p>{{ current.text }}</p>
    <details>
      <summary>查看全部要点与出处</summary>
      <p v-if="review" class="review-help">{{ review.enabled ? '核对视频，勾选确实属于这个动作的要点，供训练时提醒。' : '先确认动作，再核对要点。' }}</p>
      <ul>
        <li v-for="(tip, tipIndex) in tips" :key="tipIndex">
          <label v-if="review" class="tip-review">
            <input type="checkbox" :checked="review.approvedIndexes.includes(tipIndex)" :disabled="!review.enabled" :aria-label="`用于训练提醒：${tip.text}`" @change="reviewTip(tipIndex, $event)" />
            <span>{{ tip.text }}</span>
          </label>
          <span v-else>{{ tip.text }}</span>
          <small>{{ tip.evidence.type === 'speech' ? '视频语音' : '画面字幕' }} · {{ time(tip.evidence.start_seconds) }}–{{ time(tip.evidence.end_seconds) }}</small>
        </li>
      </ul>
    </details>
  </aside>
</template>

<style scoped>
.source-tips { margin: 12px 0; padding: 12px 14px; border-radius: 12px; background: var(--tp-surface-raised, var(--tp-surface)); color: var(--tp-ink); }
.source-tips__heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--tp-muted); font-size: 12px; }
button, summary { min-height: 44px; display: inline-flex; align-items: center; cursor: pointer; color: inherit; font-size: 12px; }
button { padding: 0 8px; border: 0; background: transparent; }
p { margin: 4px 0; font-size: 15px; line-height: 1.6; }
ul { margin: 0; padding-left: 18px; }
li { margin: 8px 0; font-size: 13px; line-height: 1.5; }
small { display: block; color: var(--tp-muted); }
.tip-review { display: flex; align-items: center; gap: 10px; min-height: 44px; cursor: pointer; }
.tip-review input { width: 20px; height: 20px; flex: 0 0 20px; accent-color: var(--tp-ink); }
.review-help { font-size: 13px; color: var(--tp-muted); }
</style>
