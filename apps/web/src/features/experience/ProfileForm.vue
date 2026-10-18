<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import type { TrainingProfile } from '@/domain/training'
import { isCompleteTrainingProfile } from './calorie'

const props = defineProps<{ profile: TrainingProfile }>()
const emit = defineEmits<{
  save: [profile: Omit<TrainingProfile, 'id' | 'updatedAt'>]
  clear: []
}>()

const form = reactive({
  trainingExperience: props.profile.trainingExperience ?? null,
  sex: props.profile.sex,
  age: props.profile.age,
  heightCm: props.profile.heightCm,
  weightKg: props.profile.weightKg,
})

watch(
  () => props.profile,
  (profile) => Object.assign(form, {
    trainingExperience: profile.trainingExperience ?? null,
    sex: profile.sex,
    age: profile.age,
    heightCm: profile.heightCm,
    weightKg: profile.weightKg,
  }),
  { deep: true },
)

const complete = computed(() => isCompleteTrainingProfile(form))
const nullableNumber = (value: string): number | null => value === '' ? null : Number(value)
</script>

<template>
  <form class="profile-form" @submit.prevent="emit('save', { ...form })">
    <div class="profile-status" :class="{ complete }">
      <span>{{ complete ? '个性化约值已开启' : '未填全时使用通用约值' }}</span>
      <i />
    </div>
    <div class="profile-grid">
      <label class="experience-field"><span>训练经验（可选）</span><select v-model="form.trainingExperience" aria-label="训练经验"><option :value="null">暂不填写</option><option value="beginner">刚开始训练</option><option value="intermediate">有一定经验</option><option value="advanced">长期规律训练</option></select></label>
      <label>
        <span>性别</span>
        <select v-model="form.sex" aria-label="性别">
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
          @input="form.weightKg = nullableNumber(($event.target as HTMLInputElement).value)"
        />
      </label>
    </div>
    <div class="profile-actions">
      <button type="button" class="quiet" @click="emit('clear')">清除档案</button>
      <button type="submit">保存档案</button>
    </div>
  </form>
</template>

<style scoped>
.profile-form { display: grid; gap: 14px; }
.profile-status,
.profile-actions { display: flex; align-items: center; }
.profile-status { gap: 7px; color: var(--muted); font-size: 11px; }
.profile-status i { width: 7px; height: 7px; border-radius: 50%; background: var(--coral); }
.profile-status.complete { color: var(--cyan); }
.profile-status.complete i { background: var(--cyan); box-shadow: 0 0 12px rgb(38 235 213 / 60%); }
.profile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.experience-field { grid-column: 1 / -1; }
.profile-grid label { display: grid; gap: 5px; color: var(--muted); font-size: 11px; }
.profile-grid input,
.profile-grid select { min-width: 0; min-height: 44px; padding: 9px 10px; border: 1px solid var(--line); border-radius: 10px; color: var(--ink); background: var(--surface-raised); }
.profile-grid select option { color: #111; }
.profile-actions { justify-content: flex-end; gap: 8px; }
.profile-actions button { min-height: 44px; padding: 0 14px; border: 1px solid var(--cyan); border-radius: 10px; color: var(--bg); background: var(--cyan); font-weight: 800; }
.profile-actions .quiet { color: var(--muted); border-color: var(--line); background: transparent; font-weight: 600; }
</style>
