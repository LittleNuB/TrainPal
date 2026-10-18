import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDraftStore } from '@/stores/draft'
import type { AdjustmentPolicyInput } from '@/personalization/adjustment-policy'

afterEach(() => vi.unstubAllGlobals())
const setup = async () => {
  setActivePinia(createPinia())
  const draft = useDraftStore()
  await draft.load({ load: async () => undefined, save: async () => {} })
  draft.addManualAction({ name: '合成划船', mode: 'reps' })
  draft.items[0]!.sets = { value: 3, source: 'video' }
  draft.items[0]!.restSeconds = { value: 60, source: 'user' }
  const input: Omit<AdjustmentPolicyInput, 'basePlan'> = {
    intent: 'more_challenging', trainingExperience: 'intermediate', contextRevision: 1,
    signals: [], hasSafetyStopSignal: false, generatedAt: new Date().toISOString(),
  }
  return { draft, input }
}
type WireInput = { input_fingerprint: string; plan_revision: number; actions: { id: string }[] }
const response = (input: WireInput, value = 11, field = 'reps') => new Response(JSON.stringify({
  input_fingerprint: input.input_fingerprint, plan_revision: input.plan_revision,
  mode: 'model', summary: '本次小幅增加一次。',
  changes: [{ action_id: input.actions[0]!.id, field, value, reason: '这次希望增加挑战，每组增加一次。' }],
}), { status: 200 })

describe('model-authored plan adjustment', () => {
  it('uses the returned value, requires confirmation, and preserves manual and video values', async () => {
    const { draft, input } = await setup()
    const fetcher = vi.fn(async (_url, init) => response(JSON.parse(init.body)))
    vi.stubGlobal('fetch', fetcher)
    const proposal = await draft.proposeModelAdjustment(input, new AbortController().signal)
    expect(fetcher).toHaveBeenCalledOnce()
    expect(proposal.origin).toBe('model')
    expect(proposal.changes[0]!.after.value).toBe(11)
    expect(draft.items[0]!.reps.value).toBe(10)
    expect(await draft.applyAdjustmentProposal(proposal.id)).toBe('applied')
    expect(draft.items[0]!.reps).toEqual({ value: 11, source: 'personalized' })
    expect(draft.items[0]!.sets).toEqual({ value: 3, source: 'video' })
    expect(draft.items[0]!.restSeconds).toEqual({ value: 60, source: 'user' })
  })

  it.each([[14, 'reps'], [4, 'sets'], [65, 'restSeconds']])('rejects invalid model value %s / %s', async (value, field) => {
    const { draft, input } = await setup()
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => response(JSON.parse(init.body), Number(value), String(field))))
    await expect(draft.proposeModelAdjustment(input, new AbortController().signal)).rejects.toThrow()
    expect(draft.adjustmentProposal).toBeNull()
    expect(draft.items[0]!.reps.value).toBe(10)
  })

  it.each(['abort', 'edit'] as const)('rejects late response after %s', async action => {
    const { draft, input } = await setup()
    let finish!: () => void
    const waiting = new Promise<void>(resolve => { finish = resolve })
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => { await waiting; return response(JSON.parse(init.body)) }))
    const controller = new AbortController()
    const pending = draft.proposeModelAdjustment(input, controller.signal)
    const rejected = expect(pending).rejects.toThrow()
    if (action === 'abort') controller.abort()
    else draft.items[0]!.reps = { value: 9, source: 'user' }
    finish()
    await rejected
    expect(draft.adjustmentProposal).toBeNull()
    expect(draft.items[0]!.reps.value).toBe(action === 'edit' ? 9 : 10)
  })

  it('never substitutes rule suggestions when the model endpoint fails', async () => {
    const { draft, input } = await setup()
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })))
    await expect(draft.proposeModelAdjustment(input, new AbortController().signal)).rejects.toThrow()
    expect(draft.adjustmentProposal).toBeNull()
  })
})
