<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import type { TrainingProfile } from '@/domain/training'

const props = defineProps<{
  profile: TrainingProfile
  busy: boolean
  saveFailed: boolean
}>()

const emit = defineEmits<{
  save: [profile: Omit<TrainingProfile, 'id' | 'updatedAt'>]
  skip: []
  clear: []
}>()

const form = reactive({
  sex: props.profile.sex,
  age: props.profile.age,
  heightCm: props.profile.heightCm,
  weightKg: props.profile.weightKg,
})

watch(
  () => props.profile,
  (profile) => Object.assign(form, {
    sex: profile.sex,
    age: profile.age,
    heightCm: profile.heightCm,
    weightKg: profile.weightKg,
  }),
  { deep: true },
)

const hasExistingProfile = computed(() => [
  props.profile.sex,
  props.profile.age,
  props.profile.heightCm,
  props.profile.weightKg,
].some((value) => value !== null))

const nullableNumber = (value: string): number | null => value === '' ? null : Number(value)
const save = (): void => emit('save', {
  sex: form.sex,
  age: form.age,
  heightCm: form.heightCm,
  weightKg: form.weightKg,
})
</script>

<template>
  <section class="profile-step">
    <div class="profile-intro">
      <p class="tp-kicker">训练档案 · 可选</p>
      <h1 class="tp-title">完善训练档案</h1>
      <p>可跳过，不影响测评结果</p>
    </div>

    <form class="profile-card tp-card" @submit.prevent="save">
      <div class="profile-grid">
        <label>
          <span>性别</span>
          <select v-model="form.sex" aria-label="性别" :disabled="busy">
            <option :value="null">暂不填写</option>
            <option value="female">女</option>
            <option value="male">男</option>
          </select>
        </label>
        <label>
          <span>年龄</span>
          <input
            :value="form.age ?? ''"
            aria-label="年龄"
            type="number"
            min="18"
            max="100"
            inputmode="numeric"
            placeholder="岁"
            :disabled="busy"
            @input="form.age = nullableNumber(($event.target as HTMLInputElement).value)"
          />
        </label>
        <label>
          <span>身高</span>
          <input
            :value="form.heightCm ?? ''"
            aria-label="身高"
            type="number"
            min="100"
            max="250"
            step="0.1"
            inputmode="decimal"
            placeholder="cm"
            :disabled="busy"
            @input="form.heightCm = nullableNumber(($event.target as HTMLInputElement).value)"
          />
        </label>
        <label>
          <span>体重</span>
          <input
            :value="form.weightKg ?? ''"
            aria-label="体重"
            type="number"
            min="20"
            max="300"
            step="0.1"
            inputmode="decimal"
            placeholder="kg"
            :disabled="busy"
            @input="form.weightKg = nullableNumber(($event.target as HTMLInputElement).value)"
          />
        </label>
      </div>

      <p class="privacy-note">
        这些信息只保存在当前设备，主要用于训练消耗约值；不会发送给 GYMTI 或结果解释模型。
      </p>
      <button
        v-if="hasExistingProfile"
        data-action="clear"
        class="clear-profile"
        type="button"
        :disabled="busy"
        @click="emit('clear')"
      >
        清除已有档案
      </button>

      <div v-if="saveFailed" class="save-failure" role="alert">
        <strong>训练档案暂时没有保存成功</strong>
        <p>测评结果已经安全保留，你可以重试，也可以不保存档案继续查看。</p>
      </div>

      <footer class="profile-actions">
        <template v-if="saveFailed">
          <button data-action="continue" class="secondary-action" type="button" :disabled="busy" @click="emit('skip')">
            不保存，继续查看
          </button>
          <button data-action="retry" class="primary-action" type="button" :disabled="busy" @click="save">
            {{ busy ? '正在保存…' : '重试保存' }}
          </button>
        </template>
        <template v-else>
          <button data-action="skip" class="secondary-action" type="button" :disabled="busy" @click="emit('skip')">
            跳过
          </button>
          <button data-action="save" class="primary-action" type="submit" :disabled="busy">
            {{ busy ? '正在保存…' : '保存并查看结果' }}
          </button>
        </template>
      </footer>
    </form>
  </section>
</template>

<style scoped>
.profile-step { display: grid; gap: 20px; padding-bottom: 112px; }
.profile-intro { display: grid; gap: 10px; padding-top: 18px; }
.profile-intro h1,
.profile-intro p { margin: 0; }
.profile-intro > p:last-child { color: var(--tp-muted); font-size: 13px; }
.profile-card { display: grid; gap: 18px; padding: 18px; box-shadow: var(--tp-shadow-soft); }
.profile-grid { display: grid; grid-template-columns: 1fr; gap: 12px; }
.profile-grid label { display: grid; gap: 7px; color: var(--tp-muted); font-size: 12px; font-weight: 700; }
.profile-grid input,
.profile-grid select { min-width: 0; min-height: 48px; padding: 10px 12px; border: 1px solid var(--tp-line); border-radius: 13px; color: var(--tp-ink); background: #F8F4EB; font: inherit; }
.privacy-note { margin: 0; padding: 13px; border-left: 3px solid var(--tp-secondary); border-radius: 0 12px 12px 0; color: var(--tp-muted); background: rgb(165 186 99 / 10%); font-size: 11px; line-height: 1.65; }
.clear-profile { width: fit-content; min-height: 44px; padding: 0; border: 0; color: var(--tp-muted); background: transparent; font-size: 12px; text-decoration: underline; text-underline-offset: 4px; }
.save-failure { display: grid; gap: 5px; padding: 13px; border: 1px solid rgb(179 38 30 / 24%); border-radius: 13px; color: var(--tp-danger); background: rgb(179 38 30 / 5%); }
.save-failure strong { font-size: 13px; }
.save-failure p { margin: 0; color: var(--tp-muted); font-size: 11px; line-height: 1.6; }
.profile-actions { position: fixed; right: max(14px, env(safe-area-inset-right)); bottom: max(14px, env(safe-area-inset-bottom)); left: max(14px, env(safe-area-inset-left)); z-index: 20; display: grid; grid-template-columns: minmax(0, .8fr) minmax(0, 1.4fr); max-width: 732px; gap: 10px; margin: auto; padding: 10px; border: 1px solid rgb(28 40 34 / 12%); border-radius: 20px; background: var(--tp-surface); box-shadow: var(--tp-shadow-float); }
.profile-actions button { min-height: 48px; border-radius: 999px; font-weight: 800; }
.secondary-action { border: 1px solid var(--tp-line); color: var(--tp-ink); background: transparent; }
.primary-action { border: 1px solid var(--tp-primary); color: #FFFDF8; background: var(--tp-primary-readable); }

@media (min-width: 360px) {
  .profile-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 1024px) { .profile-actions { left: calc(var(--tp-sidebar-width, 216px) + 24px); } }
</style>
