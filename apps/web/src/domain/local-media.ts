import type {
  AnalysisCapabilities,
  LocalMediaFingerprint,
  LocalMediaRecord,
} from '@/domain/types'

export const SUPPORTED_LOCAL_MEDIA_TYPES = [
  'video/mp4',
  'video/quicktime',
  'video/webm',
] as const

// Only CloudBase builds set this transport envelope; local servers use their
// advertised capability. Never silently apply a historical host limit locally.
export const CLOUDBASE_DEMO_UPLOAD_MAX_BYTES = 19_000_000

export type LocalMediaValidation =
  | { ok: true }
  | {
      ok: false
      code: 'disabled' | 'unsupported_type' | 'too_large' | 'too_long' | 'invalid_duration'
      message: string
    }

const formatDurationLimit = (seconds: number): string => {
  const whole = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(whole / 60)
  const remainder = whole % 60
  if (!minutes) return `${remainder} 秒`
  if (!remainder) return `${minutes} 分钟`
  return `${minutes} 分 ${remainder} 秒`
}

const formatSizeLimit = (bytes: number): string => {
  const megabytes = bytes / 1_000_000
  return `${Number.isInteger(megabytes) ? megabytes : megabytes.toFixed(1)} MB`
}

export const effectiveLocalUploadMaxBytes = (
  capabilities: AnalysisCapabilities,
): number => {
  const configured = Number(import.meta.env.VITE_UPLOAD_MAX_BYTES)
  const transportLimit = Number.isSafeInteger(configured) && configured > 0
    ? configured : capabilities.local_upload_max_bytes
  return Math.min(capabilities.local_upload_max_bytes, transportLimit)
}

export const validateLocalMediaUpload = (
  file: File,
  capabilities: AnalysisCapabilities,
): LocalMediaValidation => {
  if (!capabilities.local_upload_enabled) {
    return { ok: false, code: 'disabled', message: '当前环境暂不支持本地视频分析' }
  }
  if (!SUPPORTED_LOCAL_MEDIA_TYPES.includes(file.type as typeof SUPPORTED_LOCAL_MEDIA_TYPES[number])) {
    return {
      ok: false,
      code: 'unsupported_type',
      message: '请选择 MP4、MOV 或 WebM 视频',
    }
  }
  if (file.size > effectiveLocalUploadMaxBytes(capabilities)) {
    const platformEnvelopeIsLimiting = (
      effectiveLocalUploadMaxBytes(capabilities) === CLOUDBASE_DEMO_UPLOAD_MAX_BYTES
      && capabilities.local_upload_max_bytes > CLOUDBASE_DEMO_UPLOAD_MAX_BYTES
    )
    return {
      ok: false,
      code: 'too_large',
      message: platformEnvelopeIsLimiting
        ? 'CloudBase 演示入口限制 20 MB；请先压缩视频再上传'
        : `当前环境支持不超过 ${formatSizeLimit(effectiveLocalUploadMaxBytes(capabilities))} 的视频`,
    }
  }
  return { ok: true }
}

export const validateLocalMediaFile = (
  file: File,
  durationSeconds: number,
  capabilities: AnalysisCapabilities,
): LocalMediaValidation => {
  const uploadValidation = validateLocalMediaUpload(file, capabilities)
  if (!uploadValidation.ok) return uploadValidation
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    return { ok: false, code: 'invalid_duration', message: '无法读取这个视频的时长，请重新选择' }
  }
  if (durationSeconds > capabilities.local_analysis_max_seconds) {
    return {
      ok: false,
      code: 'too_long',
      message: `当前环境支持最长 ${formatDurationLimit(capabilities.local_analysis_max_seconds)}的视频；长视频能力尚未完成验证`,
    }
  }
  return { ok: true }
}

export const fingerprintMatches = (
  expected: LocalMediaFingerprint,
  file: File,
  durationSeconds: number,
): boolean => (
  expected.fileName === file.name
  && expected.mimeType === file.type
  && expected.sizeBytes === file.size
  && expected.lastModified === file.lastModified
  && Math.abs(expected.durationSeconds - durationSeconds) <= 1
)

export const localMediaAsFile = (record: LocalMediaRecord): File => new File(
  [record.blob],
  record.fileName,
  { type: record.mimeType, lastModified: record.lastModified },
)

export const probeVideoDuration = (file: File): Promise<number> => new Promise((resolve, reject) => {
  const url = URL.createObjectURL(file)
  const video = document.createElement('video')
  let settled = false
  const finish = (operation: () => void): void => {
    if (settled) return
    settled = true
    URL.revokeObjectURL(url)
    video.removeAttribute('src')
    operation()
  }
  const timeout = window.setTimeout(() => {
    finish(() => reject(new Error('video metadata timed out')))
  }, 10_000)
  video.preload = 'metadata'
  video.onloadedmetadata = () => {
    window.clearTimeout(timeout)
    const duration = video.duration
    finish(() => {
      if (Number.isFinite(duration) && duration > 0) resolve(duration)
      else reject(new Error('video duration unavailable'))
    })
  }
  video.onerror = () => {
    window.clearTimeout(timeout)
    finish(() => reject(new Error('video metadata unavailable')))
  }
  video.src = url
})
