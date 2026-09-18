import { afterEach, describe, expect, it, vi } from 'vitest'
import { captureVoiceCommand } from '@/training/voice-command'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const microphone = () => {
  const stop = vi.fn()
  const stream = { getTracks: () => [{ stop }] }
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } })
  return { stop, stream }
}

describe('one-shot voice capture lifecycle', () => {
  it('offers a text fallback when microphone permission is denied', async () => {
    microphone()
    vi.stubGlobal('AudioContext', class {})
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValue(new DOMException('Denied', 'NotAllowedError'))
    await expect(captureVoiceCommand(new AbortController().signal)).rejects.toThrow('改用文字')
  })
  it('stops a granted microphone if the audio context cannot initialize', async () => {
    const { stop } = microphone()
    vi.stubGlobal('AudioContext', class { constructor() { throw new Error('audio unavailable') } })
    await expect(captureVoiceCommand(new AbortController().signal)).rejects.toThrow()
    expect(stop).toHaveBeenCalledOnce()
  })
  it('stops a late permission grant after cancellation', async () => {
    const { stop, stream } = microphone()
    let grant!: (value: MediaStream) => void
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementation(() => new Promise((resolve) => { grant = resolve }))
    vi.stubGlobal('AudioContext', class {})
    const controller = new AbortController()
    const capture = captureVoiceCommand(controller.signal)
    controller.abort()
    await expect(capture).rejects.toThrow('已取消')
    grant(stream as unknown as MediaStream)
    await Promise.resolve()
    expect(stop).toHaveBeenCalledOnce()
  })
  it('ends after speech and silence, produces bounded WAV, and releases capture resources', async () => {
    const { stop } = microphone()
    const close = vi.fn().mockResolvedValue(undefined)
    const disconnect = vi.fn()
    let port: { onmessage: ((event: { data: Float32Array }) => void) | null } | undefined
    vi.stubGlobal('AudioContext', class {
      sampleRate = 16_000
      destination = {}
      audioWorklet = { addModule: async () => {} }
      resume = async () => {}
      close = close
      createMediaStreamSource() { return { connect() {}, disconnect } }
    })
    vi.stubGlobal('AudioWorkletNode', class {
      port = { onmessage: null as ((event: { data: Float32Array }) => void) | null }
      constructor() { port = this.port }
      connect() {}
      disconnect = disconnect
    })
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:voice-module')
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const capture = captureVoiceCommand(new AbortController().signal)
    for (let i = 0; i < 8; i++) await Promise.resolve()
    port!.onmessage!({ data: new Float32Array(8_000).fill(0.1) })
    port!.onmessage!({ data: new Float32Array(24_000) })
    const wav = await capture
    expect(wav.type).toBe('audio/wav')
    expect(wav.size).toBe(64_044)
    expect(stop).toHaveBeenCalledOnce()
    expect(close).toHaveBeenCalledOnce()
    expect(revoke).toHaveBeenCalledWith('blob:voice-module')
    expect(port!.onmessage).toBeNull()
  })
})
