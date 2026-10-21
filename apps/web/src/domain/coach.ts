import { appUrl } from '@/api/url'
import type { PetState } from '@/features/experience/pet-state'

export const COACH_STYLE_IDS = [
  'hotblood',
  'gentle',
  'snarky',
  'analyst',
  'comedian',
  'challenger',
  'zen',
] as const

export type CoachStyleId = typeof COACH_STYLE_IDS[number]

export const COACH_ACTIONS = {
  hotblood: ['idle', 'cheer', 'hop', 'drag'],
  gentle: ['idle', 'comfort', 'guide', 'rest'],
  snarky: ['idle', 'mock', 'point', 'annoyed'],
  analyst: ['idle', 'scan', 'analyze', 'report'],
  comedian: ['idle', 'talk', 'laugh', 'celebrate'],
  challenger: ['idle', 'challenge', 'countdown', 'power-up'],
  zen: ['idle', 'breathe', 'nod', 'relax'],
} as const satisfies Record<CoachStyleId, readonly string[]>

export type CoachAction = typeof COACH_ACTIONS[CoachStyleId][number]

export const COACH_STYLE_LABELS = {
  hotblood: '热血鼓励型',
  gentle: '温柔陪伴型',
  snarky: '毒舌督促型',
  analyst: '专业数据型',
  comedian: '幽默话痨型',
  challenger: '挑战突破型',
  zen: '佛系陪练型',
} as const satisfies Record<CoachStyleId, string>

export const COACH_ACTION_FRAME_MILLISECONDS = {
  hotblood: { idle: 180, cheer: 120, hop: 130, drag: 150 },
  gentle: { idle: 190, comfort: 145, guide: 155, rest: 190 },
  snarky: { idle: 180, mock: 145, point: 135, annoyed: 155 },
  analyst: { idle: 185, scan: 140, analyze: 150, report: 145 },
  comedian: { idle: 175, talk: 130, laugh: 125, celebrate: 120 },
  challenger: { idle: 170, challenge: 125, countdown: 135, 'power-up': 115 },
  zen: { idle: 200, breathe: 210, nod: 165, relax: 220 },
} as const satisfies Record<CoachStyleId, Record<string, number>>

export type CoachMotionEvent =
  | 'set_started'
  | 'set_completed'
  | 'rest_final_countdown'
  | 'session_completed'

export interface CoachMotionCue {
  sequence: number
  event: CoachMotionEvent
}

const groupEventActions = {
  hotblood: 'cheer',
  gentle: 'guide',
  snarky: 'point',
  analyst: null,
  comedian: 'talk',
  challenger: 'challenge',
  zen: 'nod',
} as const satisfies Record<CoachStyleId, CoachAction | null>

const sessionCompletedActions = {
  hotblood: 'hop',
  gentle: 'comfort',
  snarky: 'point',
  analyst: 'report',
  comedian: 'celebrate',
  challenger: 'power-up',
  zen: 'nod',
} as const satisfies Record<CoachStyleId, CoachAction>

export function coachFrameUrls(styleId: CoachStyleId, action: CoachAction): string[] {
  return Array.from(
    { length: 6 },
    (_, index) => appUrl(`/trainpal/pets/${styleId}/${action}/${String(index + 1).padStart(2, '0')}.webp`),
  )
}

export function coachFrameMilliseconds(styleId: CoachStyleId, action: CoachAction): number {
  const durations = COACH_ACTION_FRAME_MILLISECONDS[styleId] as Record<string, number>
  return durations[action] ?? 180
}

export function resolveCoachBaseAction(styleId: CoachStyleId, state: PetState): CoachAction {
  if (state === 'resting' && styleId === 'gentle') return 'rest'
  if (state === 'resting' && styleId === 'zen') return 'breathe'
  return 'idle'
}

export function resolveCoachEventAction(
  styleId: CoachStyleId,
  event: CoachMotionEvent,
): CoachAction | null {
  if (event === 'session_completed') return sessionCompletedActions[styleId]
  if (event === 'rest_final_countdown') {
    return styleId === 'challenger' ? 'countdown' : null
  }
  return groupEventActions[styleId]
}

export function isCoachStyleId(value: unknown): value is CoachStyleId {
  return typeof value === 'string' && COACH_STYLE_IDS.includes(value as CoachStyleId)
}

export function isCoachActionForStyle(
  styleId: CoachStyleId,
  action: CoachAction,
): boolean {
  return (COACH_ACTIONS[styleId] as readonly string[]).includes(action)
}
