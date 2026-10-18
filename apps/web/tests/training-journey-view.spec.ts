import 'fake-indexeddb/auto'
import Dexie from 'dexie'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { createHachimiDatabase, type HachimiDatabase } from '@/db/hachimi-database'
import { createDexieTrainingRepository } from '@/db/training-repository'
import { createQuickExperienceDraftItems } from '@/features/quick-experience/fixture'
import { useTrainingStore } from '@/stores/training'
import { createTrainingEngine } from '@/training/training-engine'
import TrainingView from '@/views/TrainingView.vue'

let db: HachimiDatabase
let wrapper: VueWrapper
afterEach(async () => {
  wrapper?.unmount()
  vi.useRealTimers()
  vi.restoreAllMocks()
  if (db) { db.close(); await Dexie.delete(db.name) }
})

const setup = async (legacy = false) => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-07T00:00:00Z'))
  const pinia = createPinia()
  setActivePinia(pinia)
  db = createHachimiDatabase(`workout-journey-${crypto.randomUUID()}`)
  const items = createQuickExperienceDraftItems().slice(0, 2)
  Object.assign(items[0]!, { name: '第一项', mode: 'reps', sets: { value: 2, source: 'user' },
    reps: { value: 3, source: 'user' }, durationSeconds: { value: null, source: null }, restSeconds: { value: 5, source: 'user' } })
  Object.assign(items[1]!, { name: '第二项', mode: 'duration', sets: { value: 1, source: 'user' },
    reps: { value: null, source: null }, durationSeconds: { value: 20, source: 'user' } })
  const engine = createTrainingEngine({ persistence: createDexieTrainingRepository(db),
    clock: { now: () => new Date(), monotonicMilliseconds: () => Date.now() },
    estimateCalories: () => ({ value: 0, method: 'generic' }),
  })
  const training = useTrainingStore()
  await engine.dispatch({ type: 'session.create', plan: { name: '合成训练', source: 'sample', sourcePlanId: null, items },
    coachStyleId: null, ...(legacy ? {} : { flowVersion: 'watch-v1' as const }) })
  await training.load(engine)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/training', component: TrainingView }, { path: '/plan', component: { template: '<div />' } },
  ] })
  await router.push('/training')
  await router.isReady()
  wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [pinia, router] } })
  await flushPromises()
  const advance = async (milliseconds: number) => {
    vi.setSystemTime(Date.now() + milliseconds)
    await training.tick()
    await flushPromises()
  }
  const click = async (label: string) => {
    await vi.waitFor(() => expect(wrapper.findAll('button').find(button => button.text() === label)?.attributes('disabled')).toBeUndefined())
    await wrapper.findAll('button').find(button => button.text() === label)!.trigger('click')
    await flushPromises()
    await vi.waitFor(() => expect(wrapper.find('.training-controls button:disabled').exists()).toBe(false))
  }
  return { training, advance, click }
}

it('shows the next set, actual completed progress and remaining rest through pause and resume', async () => {
  const { training, advance, click } = await setup()
  await click('准备好了')
  await advance(3000)
  // Completing between display ticks must not show six seconds for a five-second rest.
  vi.setSystemTime(Date.now() + 400)
  await click('完成本组')
  expect(wrapper.get('[aria-label="本次训练进度"]').text()).toContain('已完成 1 / 3 组')
  expect(wrapper.get('[aria-label="接下来的安排"]').text()).toContain('第一项 · 第 2 / 2 组 · 3 次')
  expect(wrapper.get('.rest-focus strong').text()).toBe('0:05')
  expect(wrapper.text()).toContain('休息结束后自动倒数 3 秒')
  await click('暂停')
  expect(wrapper.get('.rest-focus strong').text()).toBe('0:05')
  expect(wrapper.text()).toContain('休息已暂停')
  await advance(5000)
  expect(training.session?.status).toBe('paused')
  expect(wrapper.get('.rest-focus strong').text()).toBe('0:05')
  await click('继续休息')
  expect(training.session?.status).toBe('resting')
  expect(wrapper.get('[aria-label="本次训练进度"]').text()).toContain('已完成 1 / 3 组')
})

it('previews the next action during rest and waits for readiness before starting it', async () => {
  const { training, advance, click } = await setup()
  await click('准备好了')
  await advance(3000)
  await click('完成本组')
  await advance(5000)
  expect(training.session?.status).toBe('countdown')
  await advance(3000)
  await click('完成本组')
  expect(wrapper.get('[aria-label="接下来的安排"]').text()).toContain('第二项 · 第 1 / 1 组 · 20 秒')
  expect(wrapper.text()).toContain('接下来换动作')
  await advance(5000)
  expect(training.session?.status).toBe('paused')
  expect(wrapper.get('button.primary-action').text()).toBe('准备好了')
  expect(wrapper.get('[aria-label="本次训练进度"]').text()).toContain('已完成 2 / 3 组')
})

it.each([false, true])('describes manual continuation after background rest, legacy=%s', async legacy => {
  const { training, advance, click } = await setup(legacy)
  await click(legacy ? '开始本组' : '准备好了')
  if (!legacy) await advance(3000)
  await click('完成本组')
  await training.pauseForLeave()
  await flushPromises()
  expect(wrapper.text()).toContain('休息结束后等你确认，不会自动开始')
  expect(wrapper.text()).not.toContain('休息结束后自动倒数')
  await advance(5000)
  expect(training.session?.status).toBe('ready_to_continue')
})

it('does not count skipped sets as complete and resumes a timed set at its remaining duration', async () => {
  const { training, advance, click } = await setup()
  await click('准备好了')
  await advance(3000)
  await training.skipAction()
  await flushPromises()
  expect(wrapper.get('[aria-label="本次训练进度"]').text()).toContain('已完成 0 / 3 组')
  await click('准备好了')
  await advance(3000)
  await advance(1000)
  await click('暂停')
  expect(wrapper.get('.status-readout strong').text()).toBe('0:19')
  await advance(9000)
  await click('继续训练')
  expect(wrapper.get('.status-readout strong').text()).toBe('0:19')
  expect(wrapper.get('[aria-label="接下来的安排"]').text()).toContain('练完这一组，查看本次结果')
})
