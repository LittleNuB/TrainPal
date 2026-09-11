import type { DraftRepository } from '@/domain/types'
import type { HachimiDatabase } from '@/db/hachimi-database'
import { database } from '@/db/hachimi-database'
import { archiveDraft } from '@/db/plan-archive'
import { localDataEpochFence, type LocalDataEpochFence } from '@/local-data/epoch-fence'

export const createDexieDraftRepository = (
  db: HachimiDatabase,
  writeFence: LocalDataEpochFence = localDataEpochFence,
): DraftRepository => ({
  async load() {
    return db.transaction('rw', db.drafts, db.plans, async () => {
      const current = await db.drafts.get('current')
      if (!current || current.linkedPlanId || !current.items.length) return current
      writeFence.assertWritable()
      return archiveDraft(db, current)
    })
  },
  async save(plan) {
    writeFence.assertWritable()
    return db.transaction('rw', db.drafts, db.plans, () => archiveDraft(db, plan))
  },
})

export const draftRepository = createDexieDraftRepository(database)
