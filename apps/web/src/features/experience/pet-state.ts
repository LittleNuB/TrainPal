import type { PauseReason, SessionStatus, TrainingRecord } from '@/domain/training'

export type PetState = 'idle' | 'training' | 'resting' | 'paused' | 'completed'

export interface PetStateInput {
  sessionStatus: SessionStatus | null
  pauseReason?: PauseReason | null
  outcome?: TrainingRecord['outcome'] | null
}

export function derivePetState(input: PetStateInput): PetState {
  if (input.outcome === 'completed') return 'completed'

  switch (input.sessionStatus) {
    case 'active':
      return 'training'
    case 'resting':
      return 'resting'
    case 'ready_to_continue':
    case 'countdown':
      return 'paused'
    case 'paused':
      return input.pauseReason === 'before_start' || input.pauseReason === 'between_actions'
        ? 'idle'
        : 'paused'
    case null:
      return 'idle'
  }
}
