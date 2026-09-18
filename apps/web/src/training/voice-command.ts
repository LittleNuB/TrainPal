/** Single-utterance, local PCM capture. Only the finished WAV goes to our existing ASR. */
export const captureVoiceCommand = async (signal: AbortSignal): Promise<Blob> => {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
    throw new Error('当前浏览器不支持语音，请使用文字')
  }
  if (signal.aborted) throw new Error('已取消')
  const stream = await new Promise<MediaStream>((resolve, reject) => {
    const cancel = () => reject(new Error('已取消'))
    signal.addEventListener('abort', cancel, { once: true })
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false })
      .then((granted) => {
        signal.removeEventListener('abort', cancel)
        if (signal.aborted) granted.getTracks().forEach((track) => track.stop())
        else resolve(granted)
      }, () => {
        signal.removeEventListener('abort', cancel)
        reject(new Error('未能使用麦克风，请检查权限或改用文字'))
      })
  })
  if (signal.aborted) { stream.getTracks().forEach((track) => track.stop()); throw new Error('已取消') }
  let audioContext: AudioContext | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let recorder: AudioWorkletNode | null = null
  let moduleUrl: string | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let abort: (() => void) | null = null
  try {
    const context = new AudioContext({ sampleRate: 16_000 })
    audioContext = context
    if (!context.audioWorklet) throw new Error('当前浏览器不支持语音，请使用文字')
    moduleUrl = URL.createObjectURL(new Blob([`
      class CommandCapture extends AudioWorkletProcessor {
        process(inputs) {
          const channel = inputs[0]?.[0];
          if (channel) this.port.postMessage(channel.slice());
          return true;
        }
      }
      registerProcessor('trainpal-command', CommandCapture);
    `], { type: 'application/javascript' }))
    await context.audioWorklet.addModule(moduleUrl)
    await context.resume()
    if (signal.aborted) throw new Error('已取消')
    source = context.createMediaStreamSource(stream)
    recorder = new AudioWorkletNode(context, 'trainpal-command')
    source.connect(recorder)
    // The processor outputs silence; connecting keeps it scheduled, without mic echo.
    recorder.connect(context.destination)
    const chunks: Float32Array[] = []
    let sampleCount = 0
    let lastSpeech = 0
    let heardSpeech = false
    await new Promise<void>((resolve, reject) => {
      abort = () => reject(new Error('已取消'))
      signal.addEventListener('abort', abort, { once: true })
      timer = setTimeout(() => heardSpeech ? resolve() : reject(new Error('没有听清，请使用文字或再试一次')), 15_000)
      recorder!.port.onmessage = (event: MessageEvent<Float32Array>) => {
        if (signal.aborted) return
        const samples = event.data
        sampleCount += samples.length
        chunks.push(samples)
        const elapsed = sampleCount / context.sampleRate
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length)
        if (rms > 0.012) { heardSpeech = true; lastSpeech = elapsed }
        if (elapsed >= 14.8 || (heardSpeech && elapsed - lastSpeech > 1.2)) resolve()
        else if (!heardSpeech && elapsed > 5) reject(new Error('没有听清，请使用文字或再试一次'))
      }
    })
    const input = new Float32Array(sampleCount)
    let offset = 0
    for (const chunk of chunks) { input.set(chunk, offset); offset += chunk.length }
    const length = Math.min(240_000, Math.floor(sampleCount * 16_000 / context.sampleRate))
    const wav = new ArrayBuffer(44 + length * 2)
    const view = new DataView(wav)
    const word = (start: number, text: string) => [...text].forEach((char, i) => view.setUint8(start + i, char.charCodeAt(0)))
    word(0, 'RIFF'); view.setUint32(4, 36 + length * 2, true); word(8, 'WAVE'); word(12, 'fmt ')
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
    view.setUint32(24, 16_000, true); view.setUint32(28, 32_000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true)
    word(36, 'data'); view.setUint32(40, length * 2, true)
    for (let i = 0; i < length; i++) {
      const value = Math.max(-1, Math.min(1, input[Math.floor(i * context.sampleRate / 16_000)] ?? 0))
      view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true)
    }
    return new Blob([wav], { type: 'audio/wav' })
  } finally {
    if (timer) clearTimeout(timer)
    if (abort) signal.removeEventListener('abort', abort)
    if (moduleUrl) URL.revokeObjectURL(moduleUrl)
    if (recorder) { recorder.port.onmessage = null; recorder.disconnect() }
    source?.disconnect()
    stream.getTracks().forEach((track) => track.stop())
    await audioContext?.close().catch(() => {})
  }
}
