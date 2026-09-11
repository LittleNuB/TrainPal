import type { components } from '@/api/schema'
import type { DraftPersonalizationState } from '@/domain/personalization'

export type ActionMode = components['schemas']['ActionMode']
export type ValueSource = 'video' | 'rule' | 'personalized' | 'user'

export interface Segment {
  start_seconds: number
  end_seconds: number
}

export interface EvidenceSpan extends Segment {
  type: 'speech' | 'visual'
}

export interface CandidateParameters {
  mode: ActionMode | null
  sets: number | null
  reps: number | null
  duration_seconds: number | null
  rest_seconds: number | null
}

export interface AnalysisCandidate {
  id: string
  name: string
  source_id: string
  segment: Segment
  parameters: CandidateParameters
  evidence: EvidenceSpan[]
  needs_confirmation: boolean
}

export type SourceSummary = components['schemas']['SourceSummary']

export type AccessSession = Omit<
  components['schemas']['AccessSessionView'],
  'retry_after_seconds'
> & {
  retry_after_seconds: number | null
}

export interface AnalysisWarning {
  code: 'speech_unavailable' | 'visual_unavailable'
  message: string
}

export interface AnalysisError {
  code: 'configuration_error' | 'provider_error' | 'schema_error' | 'media_error' | 'timeout' | 'cancelled'
  message: string
  retryable: boolean
}

export interface AnalysisCapabilities {
  local_upload_enabled: boolean
  local_analysis_max_seconds: number
  local_upload_max_bytes: number
}

export interface CoverageGap extends Segment {
  reason: 'provider_error' | 'timeout' | 'media_error' | 'unknown'
  retryable: boolean
}

export interface AnalysisRun {
  id: string
  source_id: string
  trigger_seconds: number | null
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stage:
    | 'queued'
    | 'preparing_media'
    | 'analyzing_evidence'
    | 'expanding_window'
    | 'fusing_candidates'
    | 'completed'
    | 'failed'
    | 'cancelled'
  candidates: AnalysisCandidate[]
  warnings: AnalysisWarning[]
  empty_reason: 'no_evidence' | null
  error: AnalysisError | null
  source_duration_seconds: number
  processed_seconds: number
  discovered_candidate_count: number
  coverage_status: 'complete' | 'partial' | 'insufficient' | null
  coverage_gaps: CoverageGap[]
  created_at: string
  updated_at: string
}

export interface LocalMediaFingerprint {
  fileName: string
  mimeType: string
  sizeBytes: number
  lastModified: number
  durationSeconds: number
}

export interface LocalMediaRecord extends LocalMediaFingerprint {
  sourceId: string
  blob: Blob
  importedAt: string
  updatedAt: string
}

export interface LocalMediaRepository {
  load(sourceId: string): Promise<LocalMediaRecord | undefined>
  loadLatest(): Promise<LocalMediaRecord | undefined>
  save(record: LocalMediaRecord): Promise<void>
  replaceFromSelection(
    sourceId: string,
    expected: LocalMediaFingerprint,
    file: File,
    durationSeconds: number,
  ): Promise<LocalMediaRecord>
}

export interface SourcedValue<T> {
  value: T | null
  source: ValueSource | null
}

export interface DraftSourceRef {
  sourceId: string
  kind?: 'controlled' | 'local'
  title?: string
  originUrl?: string
  localMedia?: LocalMediaFingerprint
}

export interface DraftItem {
  id: string
  name: string
  origin?: 'quick_experience'
  sourceRef: DraftSourceRef | null
  segment: SourcedValue<Segment>
  confirmationStatus?: 'confirmed' | 'pending'
  mode: ActionMode
  sets: SourcedValue<number>
  reps: SourcedValue<number>
  durationSeconds: SourcedValue<number>
  restSeconds: SourcedValue<number>
  weightKg: SourcedValue<number>
}

export interface DraftPlan {
  id: 'current'
  name: string
  linkedPlanId: string | null
  items: DraftItem[]
  revision?: number
  personalization?: DraftPersonalizationState
  updatedAt: string
}

export interface DraftRepository {
  load(): Promise<DraftPlan | undefined>
  save(plan: DraftPlan): Promise<DraftPlan | void>
}
