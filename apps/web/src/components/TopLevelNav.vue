<script setup lang="ts">
const items = [
  { to: '/', label: '首页', mark: '01' },
  { to: '/train', label: '训练', mark: '02' },
  { to: '/mine', label: '我的', mark: '03' },
] as const
</script>

<template>
  <nav class="top-level-nav" aria-label="主要导航">
    <RouterLink
      v-for="item in items"
      :key="item.to"
      :to="item.to"
      :aria-label="item.label"
    >
      <small aria-hidden="true">{{ item.mark }}</small>
      <span>{{ item.label }}</span>
    </RouterLink>
  </nav>
</template>

<style scoped>
.top-level-nav {
  position: fixed;
  right: max(12px, env(safe-area-inset-right));
  bottom: max(12px, env(safe-area-inset-bottom));
  left: max(12px, env(safe-area-inset-left));
  z-index: 60;
  display: grid;
  width: min(calc(100% - 24px), 430px);
  min-height: 66px;
  margin: 0 auto;
  grid-template-columns: repeat(3, 1fr);
  padding: 6px;
  border: 1px solid rgb(28 40 34 / 12%);
  border-radius: 22px;
  background: rgb(255 253 248 / 94%);
  box-shadow: var(--tp-shadow-float);
  backdrop-filter: blur(18px);
}

.top-level-nav a {
  display: grid;
  min-width: 44px;
  min-height: 52px;
  place-content: center;
  gap: 2px;
  border-radius: 16px;
  color: var(--tp-muted);
  font-size: 13px;
  font-weight: 800;
  text-align: center;
  text-decoration: none;
}

.top-level-nav small {
  color: var(--tp-muted);
  font: 700 11px/1 var(--font-display);
  letter-spacing: .08em;
}

.top-level-nav a.router-link-exact-active {
  color: var(--tp-surface);
  background: var(--tp-ink);
}

.top-level-nav a.router-link-exact-active small { color: var(--tp-secondary); }

@media (min-width: 768px) {
  .top-level-nav {
    right: 24px;
    bottom: 24px;
    left: auto;
    width: 330px;
  }
}
</style>
