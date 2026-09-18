import type { PlaybackOption } from '@/domain/playback'
import type { operations } from '@/api/schema'

type PlaybackRequest = operations['select_playback_api_v1_playback_adjustments_post']['requestBody']['content']['application/json']

const post = async (path: string, body: BodyInit, signal: AbortSignal, contentType: string): Promise<unknown> => {
  const response = await fetch(`/api/v1/${path}`, { method: 'POST', body, signal,
    credentials: 'same-origin', headers: { 'Content-Type': contentType } })
  if (!response.ok) throw new Error('TrainPal 暂时没能完成查找，原片段保持不变。可以重试或手动播放。')
  return response.json()
}

export const requestPlaybackChoice = async (actionName: string, instruction: string, options: PlaybackOption[], signal: AbortSignal): Promise<string | null> => {
  const payload: PlaybackRequest = { action_name: actionName, instruction,
    options: options.map(({ id, label, start_seconds, end_seconds }) => ({ id, label, start_seconds, end_seconds })) }
  const result = await post('playback-adjustments', JSON.stringify(payload), signal, 'application/json') as { option_id?: unknown }
  if (result.option_id === null) return null
  if (typeof result.option_id !== 'string' || !options.some((option) => option.id === result.option_id)) throw new Error('没有找到可靠片段，原片段保持不变')
  return result.option_id
}

export const transcribeVoiceCommand = async (audio: Blob, signal: AbortSignal): Promise<string> => {
  const result = await post('playback-voice', audio, signal, 'audio/wav') as { text?: unknown }
  if (typeof result.text !== 'string' || !result.text.trim() || result.text.length > 240) throw new Error('没有听清，请使用文字')
  return result.text
}
