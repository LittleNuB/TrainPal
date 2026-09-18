<script setup lang="ts">
import { useRoute } from 'vue-router'

defineProps<{ desktopOnly?: boolean; analysisLabel?: string; trainingLabel?: string }>()
const route = useRoute()
const items = [
  { to: '/', label: '首页', icon: 'M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9' },
  { to: '/train', label: '训练', icon: 'M3 8v8m4-11v14m10-14v14m4-11v8M7 12h10' },
  { to: '/mine', label: '我的', icon: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M4 21v-2a8 8 0 0 1 16 0v2' },
] as const
const selected = (path: string) => path === '/train'
  ? ['/train', '/plan'].includes(route.path)
  : path === '/mine' ? ['/mine', '/personalize'].includes(route.path) : route.path === '/'
</script>

<template>
  <nav class="top-level-nav" :class="{ 'top-level-nav--desktop-only': desktopOnly }" aria-label="主要导航" data-coach-avoid>
    <RouterLink to="/" class="nav-brand" aria-label="TrainPal 首页">
      <span>TrainPal</span>
    </RouterLink>
    <div class="nav-links">
      <RouterLink
        v-for="item in items"
        :key="item.to"
        :to="item.to"
        :aria-label="item.label"
        :class="{ 'is-selected': selected(item.to) }"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="item.icon" /></svg>
        <span>{{ item.label }}</span>
      </RouterLink>
    </div>
    <div v-if="analysisLabel || trainingLabel" class="nav-tasks">
      <p>进行中</p>
      <RouterLink v-if="analysisLabel" to="/analysis"><span class="task-dot" />{{ analysisLabel }}<span aria-hidden="true">↗</span></RouterLink>
      <RouterLink v-if="trainingLabel" to="/training"><span class="task-dot" />{{ trainingLabel }}<span aria-hidden="true">↗</span></RouterLink>
    </div>
    <footer class="nav-local"><span aria-hidden="true" />保存在此设备<small>方案与训练记录仅在本机</small></footer>
  </nav>
</template>

<style scoped>
.top-level-nav { position: fixed; right: 0; bottom: 0; left: 0; z-index: 50; padding: 4px max(16px, env(safe-area-inset-right)) max(4px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); border-top: 1px solid var(--tp-line); background: var(--tp-surface); }
.top-level-nav--desktop-only { display: none; }
.nav-brand, .nav-tasks, .nav-local { display: none; }
.nav-links { display: grid; grid-template-columns: repeat(3, 1fr); }
.nav-links a { display: flex; min-width: 44px; min-height: 58px; flex-direction: column; align-items: center; justify-content: center; gap: 4px; border-radius: 6px; color: var(--tp-muted); font-size: 11px; text-decoration: none; transition: background-color 160ms ease, color 160ms ease; }
.nav-links svg { width: 21px; height: 21px; }
.nav-links a.is-selected { color: var(--tp-ink); font-weight: 600; }
@media (min-width: 1024px) {
  .top-level-nav { top: 0; right: auto; display: flex; width: var(--tp-sidebar-width, 216px); flex-direction: column; padding: 40px 18px 24px; border-top: 0; border-right: 1px solid var(--tp-line); background: var(--tp-surface); }
  .nav-brand { display: flex; align-items: center; margin: 0 14px 48px; color: var(--tp-ink); font: 600 28px/1.3 var(--font-brand); text-decoration: none; }
  .nav-brand small { display: block; margin-top: 7px; color: var(--tp-muted); font: 400 11px/1.5 var(--font-cn); }
  .brand-symbol { display: flex; width: 32px; height: 40px; align-items: center; font: 600 40px/.8 var(--font-display); letter-spacing: -.14em; }
  .brand-symbol span { color: var(--tp-primary-readable); }
  .nav-links { grid-template-columns: 1fr; gap: 6px; }
  .nav-links a { min-height: 48px; flex-direction: row; justify-content: start; gap: 14px; padding: 0 16px; font-size: 14px; }
  .nav-links a.is-selected { color: var(--tp-ink); background: var(--tp-sage-surface); }
  .nav-links a:hover { background: rgb(28 40 34 / 5%); }
  .nav-tasks { display: grid; gap: 6px; margin-top: 38px; }
  .nav-tasks p { margin: 0 12px 8px; color: var(--tp-muted); font-size: 11px; }
  .nav-tasks a { display: flex; align-items: center; gap: 8px; min-height: 48px; padding: 8px 12px; border-radius: 6px; color: var(--tp-ink); background: rgb(255 253 248 / 55%); font-size: 12px; line-height: 1.6; text-decoration: none; }
  .nav-tasks a > span:last-child { margin-left: auto; }
  .task-dot, .nav-local > span { display: inline-block; flex-shrink: 0; width: 5px; height: 5px; border-radius: 50%; background: var(--tp-success); }
  .nav-local { display: block; margin: auto 12px 0; padding-top: 32px; color: var(--tp-muted); font-size: 11px; }
  .nav-local > span { margin-right: 6px; vertical-align: 2px; }
  .nav-local small { display: block; margin-top: 7px; font-size: 11px; }
}
</style>
