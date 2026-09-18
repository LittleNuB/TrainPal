import type { DraftItem, DraftPlan } from '@/domain/types'
import type { CoachStyleId } from '@/domain/coach'

export interface SavedPlan {
  id: string
  name: string
  items: DraftItem[]
  revision?: DraftPlan['revision']
  personalization?: DraftPlan['personalization']
  createdAt: string
  updatedAt: string
}

export interface TrainingProfile {
  id: 'current'
  sex: 'male' | 'female' | null
  age: number | null
  heightCm: number | null
  weightKg: number | null
  updatedAt: string
}

export interface Preferences {
  id: 'current'
  petVisible: boolean
  coachStyleId: CoachStyleId | null
  coachPositions?: Partial<Record<'mobile' | 'desktop', { x: number; y: number }>>
  updatedAt: string
}

export interface PlanSnapshot {
  name: string
  source: 'draft' | 'saved' | 'sample'
  sourcePlanId: string | null
  items: DraftItem[]
}

export type SessionStatus = 'active' | 'resting' | 'ready_to_continue' | 'paused' | 'countdown'
export type PauseReason = 'before_start' | 'user' | 'page_hidden' | 'recovered' | 'between_actions'

export interface ActionProgress {
  itemId: string
  completedSets: number
  activeMilliseconds: number
  skipped: boolean
}

export interface TrainingSession {
  flowVersion?: 'watch-v1'
  countdownEndsAt?: string | null
  pendingCountdown?: boolean
  pausedRestSeconds?: number
  autoAdvanceSuspended?: boolean
  id: 'current'
  sessionId: string
  revision: number
  status: SessionStatus
  pauseReason: PauseReason | null
  plan: PlanSnapshot
  currentItemIndex: number
  currentSetIndex: number
  currentSetActiveMilliseconds: number
  activeStartedAt: string | null
  restStartedAt: string | null
  restEndsAt: string | null
  scheduledRestSeconds: number | null
  creditedRestMilliseconds: number
  progress: ActionProgress[]
  petId: 'hachimi'
  coachStyleId: CoachStyleId | null
  startedAt: string
  updatedAt: string
}

export interface CalorieEstimate {
  value: number
  method: 'personalized' | 'generic'
}

export interface ActionResult {
  itemId: string
  name: string
  targetSets: number
  completedSets: number
  completedReps: number | null
  completedDurationSeconds: number | null
  activeSeconds: number
  status: 'completed' | 'partial' | 'skipped'
}

export interface TrainingRecord {
  id: string
  outcome: 'completed' | 'ended_early'
  plan: PlanSnapshot
  actions: ActionResult[]
  activeSeconds: number
  creditedRestSeconds: number
  trainingDurationSeconds: number
  completedActionCount: number
  calorie: CalorieEstimate
  petId: 'hachimi'
  coachStyleId: CoachStyleId | null
  startedAt: string
  endedAt: string
}

export type TrainingEvent =
  | { type: 'session.started'; sessionId: string }
  | { type: 'set.started'; itemId: string; setIndex: number }
  | { type: 'set.completed'; itemId: string; setIndex: number }
  | { type: 'session.paused'; reason: PauseReason }
  | { type: 'rest.started'; endsAt: string }
  | { type: 'rest.finished' }
  | { type: 'action.skipped'; itemId: string }
  | { type: 'session.completed'; recordId: string }
  | { type: 'session.ended_early'; recordId: string }
