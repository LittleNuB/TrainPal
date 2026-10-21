import { UserMessageError } from '@/domain/user-message'

export async function apiFailure(response: Response, fallback: Error): Promise<Error> {
  try {
    const body = await response.json() as { code?: unknown }
    if (body.code === 'ai_budget_exhausted') return new UserMessageError('AI 体验额度已用完，可以继续编辑方案和训练。')
    if (body.code === 'ai_budget_unavailable') return new UserMessageError('AI 暂不可用，可以继续编辑方案和训练。')
  } catch { /* Keep the safe feature-specific fallback. */ }
  return fallback
}
