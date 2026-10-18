import { computed, onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'

import { validateTrainingReminderPlan, type TrainingReminderPlan } from '@/api/reminder-client'
import type { ReminderPreferences, TrainingReminderCache } from '@/domain/reminders'
import type { TrainingSession } from '@/domain/training'
import type { SourceTip } from '@/domain/types'

const HISTORY_KEY = 'trainpal:reminder-execution:v1'
const totalActiveMilliseconds = (session: TrainingSession): number => session.currentSetActiveMilliseconds
  + session.progress.reduce((total, action) => total + action.activeMilliseconds, 0)

/** Executes only the arrangement frozen into this session; never requests a model. */
export const useTrainingReminders = (options: {
  session: Ref<TrainingSession | null>
  preferences: Ref<ReminderPreferences | undefined>
  soundEnabled: Ref<boolean>
  blocked: Ref<boolean>
}) => {
  const cache = ref<TrainingReminderCache | null>(null)
  const speaking = ref(false)
  const waitingForSound = ref(false)
  const activeTip = ref<SourceTip | null>(null)
  const audioFailed = ref(false)
  const historyFailed = ref(false)
  const voice = shallowRef<SpeechSynthesisVoice | null>(null)
  const synthesis = window.speechSynthesis
  const refreshVoice = (): void => {
    try {
      voice.value = window.SpeechSynthesisUtterance
        ? synthesis?.getVoices().find(voice => voice.localService === true && /^zh(?:[-_]|$)/i.test(voice.lang)) ?? null : null
    } catch { voice.value = null }
  }
  refreshVoice()
  synthesis?.addEventListener('voiceschanged', refreshVoice)
  let generation = 0
  let utterance: SpeechSynthesisUtterance | null = null
  let speechTimeout: ReturnType<typeof setTimeout> | null = null
  let lastTrainingSpeechAt: number | null = null
  let arrangementFingerprint: string | null = null
  const consumed = new Set<number>()

  // One bounded, tab-session record of IDs/indices only; never quotes or audio.
  const saveHistory = (): boolean => {
    const sessionId = options.session.value?.sessionId
    if (!sessionId || !arrangementFingerprint || historyFailed.value) return false
    try {
      const value = JSON.stringify({ version: 1, session_id: sessionId, fingerprint: arrangementFingerprint,
        consumed: [...consumed], last_training_speech_at: lastTrainingSpeechAt })
      if (new TextEncoder().encode(value).byteLength > 8192) throw new Error('history capacity')
      sessionStorage.setItem(HISTORY_KEY, value)
      return true
    } catch { historyFailed.value = true; cancel(); return false }
  }

  const restoreHistory = (sessionId: string, fingerprint: string, eventCount: number): void => {
    const raw = sessionStorage.getItem(HISTORY_KEY)
    if (!raw) return
    if (new TextEncoder().encode(raw).byteLength > 8192) throw new Error('history capacity')
    const saved = JSON.parse(raw)
    if (saved.version !== 1 || typeof saved.session_id !== 'string') throw new Error('invalid history')
    if (saved.session_id !== sessionId) return
    if (saved.fingerprint !== fingerprint || !Array.isArray(saved.consumed) || saved.consumed.length > eventCount
      || new Set(saved.consumed).size !== saved.consumed.length
      || saved.consumed.some((index: unknown) => !Number.isSafeInteger(index) || Number(index) < 0 || Number(index) >= eventCount)
      || (saved.last_training_speech_at !== null && (typeof saved.last_training_speech_at !== 'number'
        || !Number.isFinite(saved.last_training_speech_at) || saved.last_training_speech_at < 0
        || saved.last_training_speech_at > Number.MAX_SAFE_INTEGER))) throw new Error('invalid history')
    saved.consumed.forEach((index: number) => consumed.add(index))
    lastTrainingSpeechAt = saved.last_training_speech_at
  }

  const cancel = (): void => {
    if (speechTimeout) clearTimeout(speechTimeout)
    speechTimeout = null
    const current = utterance
    utterance = null
    speaking.value = false
    waitingForSound.value = false
    if (current) {
      current.onstart = null
      current.onend = null
      current.onerror = null
      try { window.speechSynthesis?.cancel() } catch { audioFailed.value = true }
    }
  }

  watch(() => options.session.value?.sessionId, async (sessionId, previousSessionId) => {
    const currentGeneration = ++generation
    cancel()
    cache.value = null
    activeTip.value = null
    audioFailed.value = false
    historyFailed.value = false
    consumed.clear()
    lastTrainingSpeechAt = null
    arrangementFingerprint = null
    if (!sessionId && previousSessionId) {
      try {
        const raw = sessionStorage.getItem(HISTORY_KEY)
        if (raw && JSON.parse(raw).session_id === previousSessionId) sessionStorage.removeItem(HISTORY_KEY)
      } catch { /* Terminal cleanup cannot affect a saved training record. */ }
    }
    const frozen = options.session.value?.plan.reminderCache
    if (!sessionId || !frozen) return
    try {
      const copy = JSON.parse(JSON.stringify(frozen)) as TrainingReminderCache
      await validateTrainingReminderPlan(copy.input, copy.plan)
      const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(copy.plan)))
      const fingerprint = Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('')
      if (generation === currentGeneration && options.session.value?.sessionId === sessionId) {
        arrangementFingerprint = fingerprint
        try { restoreHistory(sessionId, fingerprint, copy.plan.reminders.length) }
        catch { historyFailed.value = true }
        cache.value = copy
        const current = options.session.value
        if (current.pauseReason === 'recovered') lastTrainingSpeechAt = totalActiveMilliseconds(current)
        if (!historyFailed.value) saveHistory()
      }
    } catch { /* An invalid optional arrangement leaves text and training usable. */ }
  }, { immediate: true })

  const enabled = computed(() => options.preferences.value?.confirmed === true
    && options.preferences.value.automatic_speech && options.soundEnabled.value)
  const message = computed(() => !enabled.value ? '自动提醒已关闭，文字要点仍可查看'
    : historyFailed.value ? '提醒进度暂不可保存，本次使用文字要点'
    : audioFailed.value ? '声音暂不可用，文字要点仍可查看'
      : cache.value && !voice.value ? '暂时无法播报，可查看文字要点'
      : cache.value ? (speaking.value ? '正在播报动作要点' : waitingForSound.value ? '等待声音开始，文字要点可查看' : '自动提醒已就绪')
        : '本次使用文字要点')

  const play = (event: TrainingReminderPlan['reminders'][number]): void => {
    const current = options.session.value
    const item = current?.plan.items[current.currentItemIndex]
    const action = cache.value?.input.actions.find(action => action.action_id === item?.id)
    const reviewed = action?.tips?.find(tip => tip.tip_id === event.tip_id)
    if (!item || !action || !reviewed || item.confirmationStatus !== 'confirmed'
      || action.action_revision !== (item.actionRevision ?? 0) || action.name !== item.name
      || action.source_id !== item.sourceRef?.sourceId
      || JSON.stringify(action.source_segment) !== JSON.stringify(item.segment.value)
      || action.mode !== item.mode || action.sets !== item.sets.value
      || action.reps !== (item.mode === 'reps' ? item.reps.value : null)
      || action.duration_seconds !== (item.mode === 'duration' ? item.durationSeconds.value : null)
      || !item.reviewedSourceTips?.some(tip => JSON.stringify(tip) === JSON.stringify(reviewed))
      || !item.sourceTips?.some(tip => JSON.stringify(tip) === JSON.stringify(reviewed.tip))) return
    activeTip.value = reviewed.tip
    if (!enabled.value || options.blocked.value || document.hidden || audioFailed.value || historyFailed.value) return
    // A conservative product estimate, not a measured voice duration. Never truncate the quote.
    const estimatedSeconds = Math.max(3, Math.ceil(Array.from(reviewed.tip.text).length / 4) + 1)
    if (estimatedSeconds > 12 || (event.stage === 'training' && item.mode === 'duration'
      && estimatedSeconds > (item.durationSeconds.value ?? 0) - current!.currentSetActiveMilliseconds / 1000 - 10)) return
    const trainedMilliseconds = totalActiveMilliseconds(current!)
    if (event.stage === 'training' && lastTrainingSpeechAt !== null
      && trainedMilliseconds - lastTrainingSpeechAt < 30000) return
    try {
      if (!synthesis || !voice.value || !window.SpeechSynthesisUtterance) return
      if (synthesis.speaking || synthesis.pending || synthesis.paused || utterance) return
      const next = new SpeechSynthesisUtterance(reviewed.tip.text)
      next.lang = voice.value.lang
      next.voice = voice.value
      utterance = next
      waitingForSound.value = true
      next.onstart = () => {
        if (utterance !== next) return
        const latest = options.session.value
        if (!enabled.value || options.blocked.value || document.hidden
          || latest?.sessionId !== current!.sessionId
          || latest.plan.items[latest.currentItemIndex]?.id !== item.id
          || latest.currentSetIndex !== current!.currentSetIndex
          || (event.stage === 'training'
            && latest.currentSetActiveMilliseconds - (event.active_seconds ?? 0) * 1000 > 2000)
          || (event.stage === 'training' ? latest.status !== 'active'
            : latest.status !== 'paused' || !['before_start', 'between_actions'].includes(latest.pauseReason ?? ''))) {
          cancel()
          return
        }
        waitingForSound.value = false
        speaking.value = true
        if (event.stage === 'training') {
          lastTrainingSpeechAt = totalActiveMilliseconds(latest)
          saveHistory()
        }
      }
      next.onend = () => {
        if (utterance !== next) return
        if (speechTimeout) clearTimeout(speechTimeout)
        speechTimeout = null
        utterance = null
        speaking.value = false
        waitingForSound.value = false
      }
      next.onerror = () => { if (utterance === next) { audioFailed.value = true; cancel() } }
      speechTimeout = setTimeout(() => { if (utterance === next) { audioFailed.value = true; cancel() } }, 12000)
      synthesis.speak(next)
    } catch { audioFailed.value = true; cancel() }
  }

  watch(() => ({ session: options.session.value, enabled: enabled.value, blocked: options.blocked.value, cache: cache.value }),
    ({ session, enabled, blocked }, previous) => {
      if (session?.sessionId !== previous?.session?.sessionId
        || session?.plan.items[session.currentItemIndex]?.id
          !== previous?.session?.plan.items[previous.session.currentItemIndex]?.id) {
        cancel()
        activeTip.value = null
      }
      const preparing = session?.status === 'paused' && session.currentSetIndex === 0
        && ['before_start', 'between_actions'].includes(session.pauseReason ?? '')
      if (!session || (session.status !== 'active' && !preparing)) { cancel(); return }
      // Encountered slots are consumed even while sound is disabled: never backfill them later.
      if (!enabled || blocked || document.hidden) cancel()
      const item = session.plan.items[session.currentItemIndex]
      if (session.status === 'active' && item?.mode === 'duration'
        && (item.durationSeconds.value ?? 0) * 1000 - session.currentSetActiveMilliseconds <= 10000) {
        cancel()
        return
      }
      for (const [key, event] of (cache.value?.plan.reminders ?? []).entries()) {
        if (event.action_id !== item?.id || event.set_number !== session.currentSetIndex + 1) continue
        if (consumed.has(key)) continue
        const previousSession = previous?.session
        const sameGroup = previousSession?.status === 'active'
          && previousSession.currentItemIndex === session.currentItemIndex
          && previousSession.currentSetIndex === session.currentSetIndex
        const at = (event.active_seconds ?? 0) * 1000
        const freshStart = previousSession?.status === 'countdown'
          || (previousSession?.status === 'paused'
            && ['before_start', 'between_actions'].includes(previousSession.pauseReason ?? ''))
        const due = event.trigger === 'action_prepare' ? preparing
          : event.trigger === 'set_start'
            ? session.status === 'active' && !sameGroup && freshStart && session.currentSetActiveMilliseconds === 0
            : event.trigger === 'active_elapsed' && session.status === 'active' && sameGroup
            && previousSession.currentSetActiveMilliseconds < at && session.currentSetActiveMilliseconds >= at
        if (!due) continue
        consumed.add(key)
        saveHistory()
        if (event.trigger === 'active_elapsed' && session.currentSetActiveMilliseconds - at > 2000) continue
        play(event)
      }
    })

  onBeforeUnmount(() => { generation += 1; cancel(); synthesis?.removeEventListener('voiceschanged', refreshVoice) })
  return { message, speaking, activeTip, cancel }
}
