import type { HachimiDatabase } from '@/db/hachimi-database'
import type { DraftPlan } from '@/domain/types'
import { normalizeDraftItems } from '@/domain/plan'
import { QUICK_EXPERIENCE_PLAN_NAME } from '@/features/quick-experience/fixture'

// Call inside the drafts + plans write transaction. The archive and current
// editor must either both persist or neither persist.
export const archiveDraft = async (
  db: HachimiDatabase,
  input: DraftPlan,
  createId: () => string = () => crypto.randomUUID(),
): Promise<DraftPlan> => {
  const draft = structuredClone(input)
  draft.items = normalizeDraftItems(draft.items, draft.name === QUICK_EXPERIENCE_PLAN_NAME)
  if (!draft.linkedPlanId && !draft.items.length) {
    await db.drafts.put(draft)
    return draft
  }
  if (draft.linkedPlanId) {
    const existing = await db.plans.get(draft.linkedPlanId)
    // A stale tab must not resurrect an explicitly deleted archive.
    if (!existing) throw new Error('linked plan does not exist')
    await db.plans.put({
      ...existing, name: draft.name, items: draft.items, updatedAt: draft.updatedAt,
      revision: draft.revision, personalization: draft.personalization,
    })
  } else {
    draft.linkedPlanId = createId()
    if (!draft.name.trim() || draft.name === '未命名方案') {
      draft.name = draft.items[0]?.sourceRef?.title?.replace(/\.[^.]+$/, '').trim()
        || `${draft.items[0]?.name || '我的'}训练方案`
    }
    await db.plans.add({
      id: draft.linkedPlanId, name: draft.name, items: draft.items,
      createdAt: draft.updatedAt, updatedAt: draft.updatedAt,
      revision: draft.revision, personalization: draft.personalization,
    })
  }
  await db.drafts.put(draft)
  return draft
}
