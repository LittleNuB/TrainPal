<script setup lang="ts">
const tokens = [
  { name: 'Canvas', variable: '--tp-canvas', value: '#F3EFE5' },
  { name: 'Surface', variable: '--tp-surface', value: '#FFFFFF' },
  { name: 'Ink', variable: '--tp-ink', value: '#1C2822' },
  { name: 'Primary', variable: '--tp-primary', value: '#D94B2B' },
  { name: 'Coach', variable: '--tp-secondary', value: '#A5BA63' },
  { name: 'Focus', variable: '--tp-focus', value: '#2459D6' },
] as const

const actionFixture = [
  { name: '弹力带肩部激活', target: '2 组 × 12 次', source: '视频给出' },
  { name: '上斜器械卧推', target: '3 组 × 10 次', source: 'TrainPal 建议' },
] as const
</script>

<template>
  <main class="tp-page design-gallery">
    <header>
      <p class="tp-kicker">DEV ONLY · VUE DESIGN SOURCE</p>
      <h1 class="tp-title">TrainPal<br>设计画廊</h1>
      <p class="tp-lead">这里全部是显式静态 Fixture，不连接分析 Provider，也不会成为运行时回退。</p>
    </header>

    <section aria-labelledby="tokens-title">
      <div class="gallery-heading"><span>01</span><h2 id="tokens-title">语义 Token</h2></div>
      <div class="token-grid">
        <article v-for="token in tokens" :key="token.variable">
          <i :style="{ background: `var(${token.variable})` }" />
          <b>{{ token.name }}</b>
          <code>{{ token.variable }}</code>
          <small>{{ token.value }}</small>
        </article>
      </div>
    </section>

    <section aria-labelledby="components-title">
      <div class="gallery-heading"><span>02</span><h2 id="components-title">核心组件</h2></div>
      <div class="component-stack">
        <div class="button-row">
          <button class="tp-primary-action" type="button">开始分析</button>
          <button class="tp-secondary-action" type="button">稍后再说</button>
          <button class="tp-icon-button" type="button" aria-label="更多">•••</button>
        </div>

        <article class="coach-card tp-card">
          <div class="coach-placeholder" aria-hidden="true">CAT</div>
          <div><small>TRAINPAL 教练</small><h3>今天先把动作做稳。</h3><p>未确认风格时只保留中性文字帮助，不在正式页面显示默认猫。</p></div>
        </article>

        <div class="action-list tp-card">
          <article v-for="(action, index) in actionFixture" :key="action.name">
            <span>{{ String(index + 1).padStart(2, '0') }}</span>
            <div><h3>{{ action.name }}</h3><p>{{ action.target }}</p></div>
            <small>{{ action.source }}</small>
          </article>
        </div>
      </div>
    </section>

    <section aria-labelledby="states-title">
      <div class="gallery-heading"><span>03</span><h2 id="states-title">任务状态</h2></div>
      <div class="state-grid">
        <article class="tp-card"><i class="state-dot state-dot--active" /><b>正在理解视频</b><p>已处理 22 / 38 秒 · 找到 3 个动作</p></article>
        <article class="tp-card"><i class="state-dot state-dot--partial" /><b>部分完成</b><p>保留可靠结果，并允许只重试缺口。</p></article>
        <article class="tp-card"><i class="state-dot state-dot--error" /><b>这次没有分析成功</b><p>系统失败不会伪装成“没有动作”。</p></article>
      </div>
    </section>

    <section class="training-fixture tp-training-theme" aria-labelledby="training-title">
      <p class="tp-kicker">TRAINING STAGE</p>
      <h2 id="training-title">上斜器械卧推</h2>
      <strong>02 <small>/ 03 组</small></strong>
      <p>肋骨贴稳靠背，推起时不要耸肩。</p>
      <button class="tp-primary-action" type="button">完成本组</button>
    </section>
  </main>
</template>

<style scoped>
.design-gallery { display: grid; gap: 52px; padding-bottom: 52px; }
.design-gallery > header { display: grid; gap: 16px; }
.design-gallery section:not(.training-fixture) { display: grid; gap: 18px; }
.gallery-heading { display: flex; align-items: baseline; gap: 12px; border-bottom: 1px solid var(--tp-line); padding-bottom: 12px; }
.gallery-heading span { color: var(--tp-primary); font: 700 26px/1 var(--font-display); }
.gallery-heading h2 { margin: 0; color: var(--tp-ink); font: 700 28px/1 var(--font-display), var(--font-cn); }
.token-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.token-grid article { display: grid; grid-template-columns: 42px 1fr; gap: 3px 10px; align-items: center; padding: 11px; border: 1px solid var(--tp-line); border-radius: 16px; background: var(--tp-surface); }
.token-grid i { grid-row: 1 / 4; width: 42px; height: 54px; border: 1px solid var(--tp-line); border-radius: 11px; }
.token-grid b { font-size: 13px; }
.token-grid code { overflow: hidden; color: var(--tp-muted); font-size: 11px; text-overflow: ellipsis; }
.token-grid small { color: var(--tp-muted); font: 600 11px/1 var(--font-display); }
.component-stack { display: grid; gap: 14px; }
.button-row { display: flex; flex-wrap: wrap; gap: 10px; }
.coach-card { display: grid; grid-template-columns: 72px 1fr; gap: 14px; padding: 16px; }
.coach-placeholder { display: grid; min-height: 88px; place-items: center; border-radius: 18px; color: var(--tp-training-ink); background: var(--tp-ink); font: 700 18px/1 var(--font-display); letter-spacing: .12em; }
.coach-card small { color: var(--tp-primary-readable); font: 700 11px/1 var(--font-display); letter-spacing: .1em; }
.coach-card h3 { margin: 6px 0; font-size: 17px; }
.coach-card p { margin: 0; color: var(--tp-muted); font-size: 12px; line-height: 1.6; }
.action-list { overflow: hidden; box-shadow: none; }
.action-list article { display: grid; grid-template-columns: 32px 1fr auto; align-items: center; gap: 10px; min-height: 76px; padding: 12px 14px; border-bottom: 1px solid var(--tp-line); }
.action-list article:last-child { border-bottom: 0; }
.action-list > article > span { color: var(--tp-primary-readable); font: 700 16px/1 var(--font-display); }
.action-list h3 { margin: 0 0 4px; font-size: 14px; }
.action-list p { margin: 0; color: var(--tp-muted); font-size: 12px; }
.action-list small { color: var(--tp-muted); font-size: 11px; }
.state-grid { display: grid; gap: 10px; }
.state-grid article { position: relative; padding: 16px 16px 16px 44px; box-shadow: none; }
.state-grid b { font-size: 14px; }
.state-grid p { margin: 5px 0 0; color: var(--tp-muted); font-size: 12px; line-height: 1.5; }
.state-dot { position: absolute; top: 18px; left: 18px; width: 10px; height: 10px; border-radius: 50%; }
.state-dot--active { background: var(--tp-secondary); }
.state-dot--partial { background: var(--tp-warning); }
.state-dot--error { background: var(--tp-danger); }
.training-fixture { display: grid; gap: 16px; min-height: 420px; align-content: end; margin: 0 -16px; padding: 28px 20px; border-radius: 28px; }
.training-fixture h2 { margin: 0; font-size: 27px; }
.training-fixture > strong { font: 700 78px/.9 var(--font-display); }
.training-fixture > strong small { color: #ABB4AD; font-size: 20px; }
.training-fixture > p:not(.tp-kicker) { margin: 0; color: #C8D0CA; font-size: 14px; }

@media (min-width: 640px) {
  .token-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .state-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
