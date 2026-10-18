export type AdjustmentPolicyVersion = 'adjustment-policy.v1' | 'adjustment-policy.v2' | 'adjustment-policy.v3'
export type AdjustmentIntent = 'easier_to_finish' | 'more_challenging' | 'shorter_session'
export type TrainingExperience = 'beginner' | 'intermediate' | 'advanced'
export type AdjustableField = 'sets' | 'reps' | 'durationSeconds' | 'restSeconds'
export type AdjustmentReasonCode =
  | 'reduce_to_finish'
  | 'increase_after_too_easy'
  | 'increase_for_challenge'
  | 'shorten_time_budget'
  | 'extend_recovery'

export interface AdjustmentSignal {
  itemId: string
  value: 'too_hard' | 'just_right' | 'too_easy'
  recordedAt: string
}

export interface AdjustmentChange {
  itemId: string
  itemName: string
  field: AdjustableField
  before: { value: number; source: 'rule' | 'personalized' }
  after: { value: number; source: 'personalized' }
  reasonCode: AdjustmentReasonCode
  explanation?: string
  policyBounds: { minimum: number; maximum: number; maximumDelta: number }
}

export interface AdjustmentProposal {
  origin?: 'model' | 'protected'
  summary?: string
  context?: AdjustmentContext
  id: string
  policyVersion: AdjustmentPolicyVersion
  basePlanRevision: number
  basePlanFingerprint: string
  inputFingerprint: string
  contextRevision: number
  intent: AdjustmentIntent
  status: 'ready' | 'no_change'
  changes: AdjustmentChange[]
  impact: {
    estimatedSecondsBefore: number
    estimatedSecondsAfter: number
    deltaRatio: number
  }
  generatedAt: string
}

export interface AdjustmentContext {
  version: 'adjustment-context.v1'
  trainingExperience: TrainingExperience | null
  availableMinutes: number | null
  energy: 'usual' | 'tired'
  signals: AdjustmentSignal[]
}

export interface AdjustmentReview {
  version: 'adjustment-review.v1'
  proposalId: string
  basePlanRevision: number
  basePlanFingerprint: string
  values: Array<{ itemId: string; field: AdjustableField; value: number | null; edited: boolean }>
}

export interface AppliedAdjustment {
  proposalId: string
  policyVersion: AdjustmentPolicyVersion
  intent: AdjustmentIntent
  baseValues: Array<{
    itemId: string
    field: AdjustableField
    value: number
    source: 'rule' | 'personalized'
  }>
  appliedValues: Array<{
    itemId: string
    field: AdjustableField
    value: number
  }>
  appliedAt: string
}

export interface DraftPersonalizationState {
  proposal: AdjustmentProposal | null
  applied: AppliedAdjustment | null
}

export type ApplyAdjustmentResult = 'applied' | 'conflict' | 'persist_failed' | 'invalid'
export type RestoreAdjustmentResult =
  | { status: 'busy' }
  | { status: 'restored'; count: number }
  | { status: 'nothing_to_restore' }
  | { status: 'persist_failed' }
