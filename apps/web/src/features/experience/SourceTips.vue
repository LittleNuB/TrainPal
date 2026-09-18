<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { SourceTip } from '@/domain/types'

const props = defineProps<{ tips: SourceTip[] }>()
const index = ref(0)
const current = computed(() => props.tips[index.value] ?? props.tips[0])
watch(() => JSON.stringify(props.tips), () => { index.value = 0 })
const time = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
</script>

<template>
  <aside v-if="current" class="source-tips" aria-label="视频动作要点">
    <div class="source-tips__heading"><span>来自视频 · 动作要点</span><button v-if="tips.length > 1" type="button" @click="index = (index + 1) % tips.length">下一条 {{ index + 1 }}/{{ tips.length }}</button></div>
    <p>{{ current.text }}</p>
    <details>
      <summary>查看全部要点与出处</summary>
      <ul><li v-for="tip in tips" :key="tip.text"><span>{{ tip.text }}</span><small>{{ tip.evidence.type === 'speech' ? '视频语音' : '画面字幕' }} · {{ time(tip.evidence.start_seconds) }}–{{ time(tip.evidence.end_seconds) }}</small></li></ul>
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
</style>
