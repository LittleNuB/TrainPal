<script setup lang="ts">
import { ref } from 'vue'

import { useAccessStore } from '@/stores/access'

const access = useAccessStore()
const expanded = ref(false)
const code = ref('')

const submit = async (): Promise<void> => {
  if (await access.upgrade(code.value)) {
    code.value = ''
    expanded.value = false
  }
}
</script>

<template>
  <section class="access-status" :class="`tier-${access.tier}`" aria-label="实时 AI 访问状态">
    <div class="access-summary">
      <span class="access-dot" />
      <p>
        <strong>{{ access.message }}</strong>
        <small v-if="access.tier === 'public'">评委可输入共享体验码使用预留通道</small>
        <small v-else>验证状态仅保存在安全 Cookie 中</small>
      </p>
      <button
        v-if="access.tier === 'public'"
        type="button"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : '评委入口' }}
      </button>
    </div>

    <form v-if="expanded && access.tier === 'public'" @submit.prevent="submit">
      <label>
        <span>评委体验码</span>
        <input
          v-model="code"
          type="password"
          autocomplete="one-time-code"
          aria-label="评委体验码"
          placeholder="输入共享体验码"
        />
      </label>
      <button type="submit" :disabled="!code.trim() || access.pending">
        {{ access.pending ? '正在校验…' : '启用预留通道' }}
      </button>
    </form>

    <div v-if="!access.canAnalyze && access.loaded" class="access-fallback">
      <RouterLink to="/train">查看预设方案</RouterLink>
      <button type="button" :disabled="access.pending" @click="access.load()">刷新名额</button>
    </div>
    <p v-if="access.errorMessage" class="access-error" role="alert">{{ access.errorMessage }}</p>
  </section>
</template>

<style scoped>
.access-status { width: 100%; margin: 0 auto 14px; padding: 10px 12px; border: 1px solid var(--tp-line); border-radius: 15px; color: var(--tp-ink); background: var(--tp-surface); }
.access-summary { display: flex; align-items: center; gap: 9px; }
.access-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: var(--tp-primary); box-shadow: 0 0 0 4px rgb(25 205 192 / 10%); }
.tier-judge .access-dot { background: var(--tp-secondary); box-shadow: 0 0 0 4px rgb(25 205 192 / 14%); }
.access-summary p { min-width: 0; flex: 1; margin: 0; }
.access-summary strong,
.access-summary small { display: block; }
.access-summary strong { font-size: 11px; }
.access-summary small { margin-top: 2px; color: var(--tp-muted); font-size: 11px; }
.access-summary button,
.access-fallback button { min-width: 44px; min-height: 44px; padding: 0 7px; border: 0; color: var(--tp-focus); background: transparent; font-size: 11px; }
.access-status form { display: grid; grid-template-columns: 1fr auto; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--tp-line); }
.access-status form label { display: grid; gap: 4px; color: var(--tp-muted); font-size: 11px; }
.access-status form input { min-width: 0; min-height: 44px; padding: 0 10px; border: 1px solid var(--tp-line); border-radius: 9px; color: var(--tp-ink); background: #F1F6FA; }
.access-status form > button { min-height: 44px; align-self: end; padding: 0 11px; border: 0; border-radius: 9px; color: var(--tp-on-primary); background: var(--tp-primary); font-size: 11px; font-weight: 800; }
.access-status form > button:disabled { opacity: .5; }
.access-fallback { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; border-top: 1px solid var(--tp-line); }
.access-fallback a { display: inline-grid; min-height: 44px; place-items: center; color: var(--tp-primary-readable); font-size: 11px; font-weight: 700; text-decoration: none; }
.access-error { margin: 6px 0 0; color: var(--tp-danger); font-size: 11px; }

@media (max-width: 480px) {
  .access-status form { grid-template-columns: 1fr; }
}
</style>
