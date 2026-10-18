import { afterEach, describe, expect, it, vi } from 'vitest'
import { installLocalWake, localWakeAvailability, startLocalWake, wakeCommand } from '@/training/voice-wake'

class LocalRecognition {
  static available = vi.fn().mockResolvedValue('available')
  static install = vi.fn().mockResolvedValue(true)
  static current: LocalRecognition
  get processLocally() { return this.local }
  set processLocally(value: boolean) { this.local = value }
  local = false
  lang = ''
  continuous = false
  interimResults = true
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: ((event: { error: string }) => void) | null = null
  onresult: ((event: { resultIndex: number; results: { isFinal: boolean; 0: { transcript: string } }[] }) => void) | null = null
  start = vi.fn(() => { this.onstart?.() })
  abort = vi.fn()
  constructor() { LocalRecognition.current = this }
  say(text: string, final = true) { this.onresult?.({ resultIndex: 0, results: [{ isFinal: final, 0: { transcript: text } }] }) }
}
const setup = () => {
  vi.useFakeTimers()
  vi.stubGlobal('SpeechRecognition', LocalRecognition)
  const callbacks = { onReady: vi.fn(), onWake: vi.fn(), onCommand: vi.fn(), onTimeout: vi.fn(), onError: vi.fn() }
  const stop = startLocalWake(callbacks)
  return { callbacks, stop, recognition: LocalRecognition.current }
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); vi.useRealTimers() })

describe('opt-in local voice wake', () => {
  it('never starts a remote-only engine or downloads during a capability check', async () => {
    vi.stubGlobal('SpeechRecognition', class { static available = LocalRecognition.available })
    expect(await localWakeAvailability()).toBe('unavailable')
    expect(LocalRecognition.install).not.toHaveBeenCalled()
    expect(() => startLocalWake({ onReady: vi.fn(), onWake: vi.fn(), onCommand: vi.fn(), onTimeout: vi.fn(), onError: vi.fn() })).toThrow()
    vi.stubGlobal('SpeechRecognition', LocalRecognition)
    expect(await localWakeAvailability()).toBe('available')
    expect(LocalRecognition.available).toHaveBeenCalledWith({ langs: ['zh-CN'], processLocally: true })
    expect(LocalRecognition.install).not.toHaveBeenCalled()
    expect(await installLocalWake()).toBe(true)
  })
  it('ignores ambient and interim speech and consumes only one wake-prefixed command', () => {
    const { recognition, callbacks } = setup()
    expect(recognition.processLocally).toBe(true)
    recognition.say('重看呼吸讲解')
    recognition.say('我今天使用 TrainPal 训练')
    recognition.say('TrainPal 继续训练', false)
    expect(callbacks.onWake).not.toHaveBeenCalled()
    recognition.say('Train Pal，重看呼吸讲解')
    recognition.say('TrainPal，继续训练')
    expect(callbacks.onWake).toHaveBeenCalledOnce()
    expect(callbacks.onCommand).toHaveBeenCalledExactlyOnceWith('重看呼吸讲解')
    expect(recognition.abort).toHaveBeenCalledOnce()
  })
  it('allows a name then one command, with a bounded waiting window', async () => {
    const { recognition, callbacks, stop } = setup()
    recognition.say('TrainPal')
    expect(callbacks.onWake).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(8_001)
    expect(callbacks.onTimeout).toHaveBeenCalledOnce()
    recognition.say('继续训练')
    expect(callbacks.onCommand).not.toHaveBeenCalled()
    recognition.say('TrainPal')
    recognition.say('暂停')
    expect(callbacks.onCommand).toHaveBeenCalledExactlyOnceWith('暂停')
    stop()
  })
  it('restarts after a normal speech service end but cannot restart after stop or permission loss', async () => {
    const { recognition, callbacks, stop } = setup()
    recognition.onend?.()
    await vi.advanceTimersByTimeAsync(401)
    expect(recognition.start).toHaveBeenCalledTimes(2)
    recognition.onerror?.({ error: 'not-allowed' })
    expect(callbacks.onError).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(20_000)
    expect(recognition.start).toHaveBeenCalledTimes(2)
    stop()
  })
  it('clears callbacks and pending timers when the owner leaves', async () => {
    const { recognition, callbacks, stop } = setup()
    recognition.say('TrainPal')
    recognition.onend?.()
    stop()
    await vi.advanceTimersByTimeAsync(20_000)
    expect(callbacks.onTimeout).not.toHaveBeenCalled()
    expect(recognition.start).toHaveBeenCalledOnce()
    expect(recognition.onresult).toBeNull()
  })
  it('requires a complete name at the start of a command', () => {
    expect(wakeCommand('TrainPalace，继续')).toBeNull()
    expect(wakeCommand('Hey TrainPal，暂停')).toBe('暂停')
    expect(wakeCommand('TrainPal，确认')).toBe('确认')
  })
})
