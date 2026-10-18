import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  AnalysisApiError,
  accessClient,
  type AccessClient,
} from '@/api/client'
import type { AccessSession } from '@/domain/types'

export const useAccessStore = defineStore('access', () => {
  const session = ref<AccessSession | null>(null)
  const loaded = ref(false)
  const pending = ref(false)
  const errorMessage = ref('')

  const tier = computed(() => session.value?.tier ?? 'public')
  const canAnalyze = computed(() => session.value?.can_analyze ?? false)
  const retryAfterSeconds = computed(() => session.value?.retry_after_seconds ?? null)
  const message = computed(() => {
    if (!loaded.value) return '正在确认实时 AI 名额'
    if (tier.value === 'judge') {
      if (canAnalyze.value) return '评委实时 AI 通道已启用'
      return retryAfterSeconds.value
        ? `评委通道正忙，约 ${retryAfterSeconds.value} 秒后可重试`
        : '评委实时 AI 名额暂不可用'
    }
    if (canAnalyze.value) return '公开实时 AI 可用'
    return retryAfterSeconds.value
      ? `公开名额正忙，约 ${retryAfterSeconds.value} 秒后可重试`
      : '公开实时 AI 名额暂不可用'
  })

  async function load(client: AccessClient = accessClient): Promise<void> {
    pending.value = true
    errorMessage.value = ''
    try {
      session.value = await client.getSession()
    } catch {
      session.value = null
      errorMessage.value = '无法确认实时 AI 名额，可先查看预设方案'
    } finally {
      loaded.value = true
      pending.value = false
    }
  }

  async function upgrade(
    code: string,
    client: AccessClient = accessClient,
  ): Promise<boolean> {
    const normalizedCode = code.trim()
    if (!normalizedCode || pending.value) return false
    pending.value = true
    errorMessage.value = ''
    try {
      session.value = await client.upgrade(normalizedCode)
      loaded.value = true
      return true
    } catch (error) {
      if (!(error instanceof AnalysisApiError)) {
        errorMessage.value = '体验码校验失败，请稍后重试'
      } else if (error.status === 401) {
        errorMessage.value = '体验码无效'
      } else if (error.status === 403) {
        errorMessage.value = '当前页面来源无效，请从正式入口重新打开'
      } else if (error.status === 429) {
        errorMessage.value = error.retryAfterSeconds === null
          ? '体验码尝试过于频繁，请稍后重试'
          : `体验码尝试过于频繁，请 ${error.retryAfterSeconds} 秒后重试`
      } else if (error.status === 422) {
        errorMessage.value = '体验码格式无效'
      } else {
        errorMessage.value = '体验码校验失败，请稍后重试'
      }
      return false
    } finally {
      pending.value = false
    }
  }

  return {
    session,
    loaded,
    pending,
    errorMessage,
    tier,
    canAnalyze,
    retryAfterSeconds,
    message,
    load,
    upgrade,
  }
})
