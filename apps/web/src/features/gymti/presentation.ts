import { appUrl } from '@/api/url'
import {
  COACH_STYLE_IDS,
  COACH_STYLE_LABELS,
  type CoachStyleId,
} from '@/domain/coach'
import type { GymtiType } from '@/domain/gymti'

import { GYMTI_QUESTIONNAIRE } from './contract'

export interface GymtiTypePresentation {
  label: string
  shortDescription: string
  illustrationUrl: string
}

const gymtiTypeCopy = {
  IMNB: {
    shortDescription: '把每次训练都当作肌肉增长的认真工程。',
    illustrationUrl: '/gymti/types/imnb.webp',
  },
  KCAL: {
    shortDescription: '相信稳定记录和可见趋势，能让减脂更踏实。',
    illustrationUrl: '/gymti/types/kcal.webp',
  },
  HIDE: {
    shortDescription: '需要更低的启动压力，也需要更清楚的新手入口。',
    illustrationUrl: '/gymti/types/hide.webp',
  },
  LIFE: {
    shortDescription: '先让身体恢复电量，再把训练慢慢放回生活。',
    illustrationUrl: '/gymti/types/life.webp',
  },
  CURV: {
    shortDescription: '在力量、体态和好看之间追求细腻平衡。',
    illustrationUrl: '/gymti/types/curv.webp',
  },
  BOOM: {
    shortDescription: '用心率、汗水和节奏，把积攒的情绪释放出去。',
    illustrationUrl: '/gymti/types/boom.webp',
  },
  WINN: {
    shortDescription: '喜欢用数据、进步和目标，把训练做成长期胜局。',
    illustrationUrl: '/gymti/types/winn.webp',
  },
} as const satisfies Record<GymtiType, Omit<GymtiTypePresentation, 'label'>>

export const GYMTI_TYPE_PRESENTATION = Object.fromEntries(
  GYMTI_QUESTIONNAIRE.gymtiTypes.map(({ id, label }) => [
    id,
    {
      label,
      ...gymtiTypeCopy[id],
      illustrationUrl: appUrl(gymtiTypeCopy[id].illustrationUrl),
    },
  ]),
) as Record<GymtiType, GymtiTypePresentation>

export interface CoachStylePresentation {
  label: string
  personality: string
  matchReason: string
}

const coachStyleCopy = {
  hotblood: {
    personality: '把每次开始都点燃成一次小胜利。',
    matchReason: '适合需要明确鼓励和节奏带动的训练时刻。',
  },
  gentle: {
    personality: '先接住你的状态，再陪你一步步开始。',
    matchReason: '适合重视安全感、低压力启动与稳定陪伴。',
  },
  snarky: {
    personality: '用有分寸的吐槽，把那些熟悉的借口戳破。',
    matchReason: '适合接受轻微嘴硬式提醒、需要直接推动。',
  },
  analyst: {
    personality: '把训练量、趋势和恢复讲得具体清楚。',
    matchReason: '适合偏爱明确依据、进步记录和清晰反馈。',
  },
  comedian: {
    personality: '用笑点化解枯燥，但不会抢走训练重点。',
    matchReason: '适合需要轻松氛围与关键节点互动。',
  },
  challenger: {
    personality: '盯住下一次突破，也尊重你说出的边界。',
    matchReason: '适合被具体目标和临门一脚激发。',
  },
  zen: {
    personality: '把训练放回长期生活，不催促，也不放弃。',
    matchReason: '适合小剂量、可持续和低压力节奏。',
  },
} as const satisfies Record<CoachStyleId, Omit<CoachStylePresentation, 'label'>>

export const COACH_STYLE_PRESENTATION = Object.fromEntries(
  COACH_STYLE_IDS.map((styleId) => [
    styleId,
    {
      label: COACH_STYLE_LABELS[styleId],
      ...coachStyleCopy[styleId],
    },
  ]),
) as Record<CoachStyleId, CoachStylePresentation>

const reasonCopy: Record<string, string> = {
  goal_strength: '你更看重力量增长和可持续的训练积累。',
  goal_body_composition: '你希望用稳定行动看到身体趋势的变化。',
  goal_confidence: '更清楚的入口和更低的压力会让你更容易开始。',
  goal_vitality: '你希望训练为日常状态充电，而不是继续消耗自己。',
  goal_body_shape: '你在意力量、体态与身体线条的共同变化。',
  goal_emotional_release: '有节奏地动起来，是你释放情绪和找回状态的方式。',
  goal_progress_tracking: '清晰目标和可见进步，会让你更愿意长期投入。',
  preference_energizing: '明确、积极的鼓励更能带动你的训练节奏。',
  preference_gentle: '先被理解和接住，会让你更愿意继续往前。',
  preference_direct: '直接、有分寸的提醒更容易把你从犹豫中拉回来。',
  preference_data: '具体数据和清楚依据会让反馈更可信。',
  preference_humor: '轻松幽默的互动能减少训练里的枯燥感。',
  preference_challenge: '清晰挑战和临门推动更容易激发你的行动。',
  preference_low_pressure: '低压力、可持续的节奏更适合你的长期坚持。',
}

const genericReason = '这个方向与你当前表达的训练偏好更贴近。'

export function gymtiReasonText(reasonCode: string): string {
  return reasonCopy[reasonCode] ?? genericReason
}
