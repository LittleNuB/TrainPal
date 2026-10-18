/** Local-only wake recognition. Never fall back to a browser's remote speech service. */
interface Recognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  processLocally: boolean
  onstart: (() => void) | null
  onend: (() => void) | null
  onerror: ((event: { error: string }) => void) | null
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null
  start(): void
  abort(): void
}
interface RecognitionConstructor {
  new(): Recognition
  available?: (options: { langs: string[]; processLocally: boolean }) => Promise<string>
  install?: (options: { langs: string[]; processLocally: boolean }) => Promise<boolean>
}
const options = { langs: ['zh-CN'], processLocally: true }
const constructor = (): RecognitionConstructor | undefined => {
  const browser = window as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor }
  const engine = browser.SpeechRecognition ?? browser.webkitSpeechRecognition
  return engine && 'processLocally' in engine.prototype && engine.available && engine.install ? engine : undefined
}

export const localWakeAvailability = async (): Promise<'available' | 'downloadable' | 'unavailable'> => {
  try {
    const engine = constructor()
    if (!engine) return 'unavailable'
    const result = await engine.available!(options)
    return result === 'available' ? 'available' : result === 'downloadable' || result === 'downloading' ? 'downloadable' : 'unavailable'
  } catch { return 'unavailable' }
}
export const installLocalWake = async (): Promise<boolean> => {
  try { return await constructor()?.install?.(options) ?? false } catch { return false }
}

// Require the name at the start of an utterance; a casual mention must not execute a command.
export const wakeCommand = (text: string): string | null => {
  const match = text.trim().match(/^(?:hey[\s,，]*|嘿[，,\s]*)?train\s*pal\b[，,。.!！:：\s]*(.*)$/i)
  return match ? match[1]!.trim() : null
}

export const startLocalWake = (callbacks: {
  onReady: () => void
  onWake: () => void
  onCommand: (text: string) => void
  onTimeout: () => void
  onError: (message: string) => void
}): (() => void) => {
  const Engine = constructor()
  if (!Engine) throw new Error('这个浏览器暂不支持免手模式，可以点一下说话。')
  const recognition = new Engine()
  recognition.lang = 'zh-CN'
  recognition.processLocally = true
  recognition.continuous = true
  recognition.interimResults = false
  let stopped = false
  let awaiting = false
  let restart: ReturnType<typeof setTimeout> | undefined
  let deadline: ReturnType<typeof setTimeout> | undefined
  let startDeadline: ReturnType<typeof setTimeout> | undefined
  const stop = () => {
    stopped = true
    clearTimeout(restart)
    clearTimeout(deadline)
    clearTimeout(startDeadline)
    recognition.onstart = recognition.onend = recognition.onerror = recognition.onresult = null
    recognition.abort()
  }
  const fail = (message: string) => { stop(); callbacks.onError(message) }
  const start = () => {
    if (stopped) return
    try {
      startDeadline = setTimeout(() => fail('麦克风未能启动，请检查权限后重新开启。'), 10_000)
      recognition.start()
    } catch { fail('免手模式已停止，请重新开启。') }
  }
  recognition.onstart = () => { clearTimeout(startDeadline); if (!stopped && !awaiting) callbacks.onReady() }
  recognition.onerror = ({ error }) => {
    if (stopped || error === 'no-speech') return
    fail(error === 'not-allowed' || error === 'service-not-allowed'
      ? '无法使用麦克风，请允许麦克风权限后重试。' : '免手模式已停止，可以点一下说话或重新开启。')
  }
  recognition.onend = () => {
    clearTimeout(startDeadline)
    if (!stopped) restart = setTimeout(start, 400)
  }
  recognition.onresult = (event) => {
    if (stopped) return
    for (let index = event.resultIndex; index < event.results.length; index++) {
      const result = event.results[index]!
      if (!result.isFinal) continue
      const text = result[0].transcript.trim()
      const command = wakeCommand(text)
      if (command === null && !awaiting) continue
      if (!awaiting) callbacks.onWake()
      if (stopped) return
      const request = command ?? text
      clearTimeout(deadline)
      if (request) {
        // One final utterance per request; the owner starts a fresh listener after processing.
        stop()
        callbacks.onCommand(request.slice(0, 240))
        return
      }
      awaiting = true
      deadline = setTimeout(() => {
        awaiting = false
        callbacks.onTimeout()
        callbacks.onReady()
      }, 8_000)
    }
  }
  start()
  return stop
}
