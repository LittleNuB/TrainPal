<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { reminderInput } from '@/domain/reminders'
import { useDraftStore } from '@/stores/draft'
import { useLibraryStore } from '@/stores/library'

const props = defineProps<{ suspended: boolean }>()
const draft = useDraftStore()
const library = useLibraryStore()
const preferencePending = ref(false)
const preferenceError = ref('')
const status = ref<'off' | 'review' | 'preparing' | 'ready' | 'failed'>('off')
const preferences = computed(() => library.preferences.speechReminders ?? { automatic_speech: false, confirmed: false })
const enabled = computed(() => preferences.value.confirmed && preferences.value.automatic_speech)
const input = computed(() => reminderInput(draft.plan, preferences.value))
const signature = computed(() => JSON.stringify(input.value))
let controller: AbortController | null = null
let generation = 0
let failedSignature: string | null = null

const arrangeReminders = async (): Promise<void> => {
  const version = ++generation
  controller?.abort()
  controller = null
  if (!enabled.value) { failedSignature = null; status.value = 'off'; return }
  if (!input.value.actions.some(action => action.confirmed && action.tips?.length)) {
    status.value = 'review'; return
  }
  if (props.suspended) { status.value = 'preparing'; return }
  const expected = signature.value
  if (failedSignature === expected) { status.value = 'failed'; return }
  const request = new AbortController()
  controller = request
  status.value = 'preparing'
  const ready = await draft.prepareTrainingReminders(preferences.value, request.signal)
  if (request.signal.aborted || version !== generation || signature.value !== expected) return
  try {
    if (ready) await draft.flushPersist()
    if (request.signal.aborted || version !== generation || signature.value !== expected) return
    status.value = ready ? 'ready' : 'failed'
    failedSignature = ready ? null : expected
  } catch {
    if (!request.signal.aborted && version === generation) {
      status.value = 'failed'
      failedSignature = expected
    }
  }
}
watch([signature, () => props.suspended], () => { void arrangeReminders() }, { immediate: true })

const changePreference = async (event: Event): Promise<void> => {
  if (preferencePending.value) return
  preferencePending.value = true
  preferenceError.value = ''
  const enabledRequested = (event.target as HTMLInputElement).checked
  try { await library.setSpeechReminders(enabledRequested) }
  catch { preferenceError.value = enabledRequested
    ? '开关没有保存成功，请重试。仍可查看文字要点。'
    : '这次已关闭播报。开关没有保存成功，请重试，避免下次打开恢复旧设置。' }
  finally {
    (event.target as HTMLInputElement).checked = enabled.value
    preferencePending.value = false
  }
}
const retry = (): void => {
  if (status.value !== 'failed' || props.suspended) return
  failedSignature = null
  void arrangeReminders()
}
onBeforeUnmount(() => { generation += 1; controller?.abort() })
</script>

<template>
  <section class="reminder-settings" aria-label="训练提醒" data-coach-avoid>
    <label>
      <span><strong>训练时听动作要点</strong><small>只用你核对过的视频内容，不影响训练进度。</small></span>
      <input type="checkbox" aria-label="训练时听动作要点" :checked="enabled" :disabled="preferencePending" @change="changePreference" />
    </label>
    <p v-if="preferenceError" role="alert">{{ preferenceError }}</p>
    <p v-else-if="status === 'review'" role="status">先在动作中核对要点，再安排提醒。</p>
    <p v-else-if="status === 'preparing'" role="status">正在安排提醒；也可以先开始，查看文字要点。</p>
    <p v-else-if="status === 'ready'" role="status">提醒安排已保存<span v-if="draft.plan.reminderCache?.plan.mode === 'rules'"> · 按组分散提醒</span></p>
    <p v-else-if="status === 'failed'" role="status">提醒暂不可用，仍可训练和查看文字要点。<button type="button" @click="retry">重试安排</button></p>
  </section>
</template>

<style scoped>
.reminder-settings { padding: 16px 0; border-top: 1px solid var(--tp-line); color: var(--tp-ink); }
label { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 44px; cursor: pointer; }
strong { display: block; font-size: 15px; font-weight: 600; }
small, p { font-size: 12px; line-height: 1.6; color: var(--tp-muted); }
small { display: block; margin-top: 3px; }
input { width: 22px; height: 22px; flex: 0 0 22px; accent-color: var(--tp-ink); }
p { margin: 8px 0 0; }
button { min-height: 44px; padding: 0 8px; border: 0; background: transparent; color: var(--tp-ink); text-decoration: underline; cursor: pointer; }
</style>
