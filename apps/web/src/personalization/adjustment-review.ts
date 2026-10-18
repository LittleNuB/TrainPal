import type { AdjustmentProposal, AdjustmentReview } from '@/domain/personalization'

export const createAdjustmentReview = (proposal: AdjustmentProposal): AdjustmentReview => ({
  version: 'adjustment-review.v1',
  proposalId: proposal.id,
  basePlanRevision: proposal.basePlanRevision,
  basePlanFingerprint: proposal.basePlanFingerprint,
  values: proposal.changes.map(change => ({
    itemId: change.itemId, field: change.field, value: change.after.value, edited: false,
  })),
})

export const validReview = (review: AdjustmentReview, proposal: AdjustmentProposal): boolean => (
  review.version === 'adjustment-review.v1'
  && review.proposalId === proposal.id
  && review.basePlanRevision === proposal.basePlanRevision
  && review.basePlanFingerprint === proposal.basePlanFingerprint
  && review.values.length === proposal.changes.length
  && new Set(review.values.map(value => `${value.itemId}:${value.field}`)).size === review.values.length
  && review.values.every(value => {
    const change = proposal.changes.find(change => change.itemId === value.itemId && change.field === value.field)
    return change && value.value !== null && Number.isSafeInteger(value.value)
      && value.value >= (value.field === 'restSeconds' ? 0 : 1)
      && (value.edited || value.value === change.after.value)
  })
)
