import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  CLOUDBASE_DEMO_UPLOAD_MAX_BYTES,
  effectiveLocalUploadMaxBytes,
  validateLocalMediaFile,
} from '@/domain/local-media'

const capabilities = {
  local_upload_enabled: true,
  local_analysis_max_seconds: 60,
  local_upload_max_bytes: 10,
}

describe('local media file gate', () => {
  afterEach(() => vi.unstubAllEnvs())

  it('accepts a 28 MB local video when the current server allows it', () => {
    const file = new File(['x'], '训练.mp4', { type: 'video/mp4' })
    Object.defineProperty(file, 'size', { value: 28_000_000 })
    expect(validateLocalMediaFile(file, 208, {
      local_upload_enabled: true, local_analysis_max_seconds: 300,
      local_upload_max_bytes: 268_435_456,
    })).toEqual({ ok: true })
  })
  it('accepts a supported file inside the deployed size and duration limits', () => {
    const file = new File(['12345'], '训练.mp4', { type: 'video/mp4' })
    expect(validateLocalMediaFile(file, 59.9, capabilities)).toEqual({ ok: true })
  })

  it('rejects unsupported type, bytes, and duration with user-safe deployed limits', () => {
    expect(validateLocalMediaFile(
      new File(['x'], '训练.avi', { type: 'video/x-msvideo' }),
      10,
      capabilities,
    )).toMatchObject({ ok: false, code: 'unsupported_type' })
    expect(validateLocalMediaFile(
      new File(['12345678901'], '训练.mp4', { type: 'video/mp4' }),
      10,
      capabilities,
    )).toMatchObject({ ok: false, code: 'too_large' })
    expect(validateLocalMediaFile(
      new File(['x'], '训练.mp4', { type: 'video/mp4' }),
      61,
      capabilities,
    )).toEqual({
      ok: false,
      code: 'too_long',
      message: '当前环境支持最长 1 分钟的视频；长视频能力尚未完成验证',
    })
  })

  it('leaves multipart headroom below the CloudBase 20 MB request envelope', () => {
    vi.stubEnv('VITE_UPLOAD_MAX_BYTES', '19000000')
    const cloudCapabilities = {
      ...capabilities,
      local_analysis_max_seconds: 300,
      local_upload_max_bytes: 256 * 1024 * 1024,
    }
    const fileAtLimit = new File(['x'], '五分钟.mp4', { type: 'video/mp4' })
    Object.defineProperty(fileAtLimit, 'size', { value: CLOUDBASE_DEMO_UPLOAD_MAX_BYTES })
    const fileOverLimit = new File(['x'], '五分钟.mp4', { type: 'video/mp4' })
    Object.defineProperty(fileOverLimit, 'size', { value: CLOUDBASE_DEMO_UPLOAD_MAX_BYTES + 1 })

    expect(effectiveLocalUploadMaxBytes(cloudCapabilities)).toBe(19_000_000)
    expect(validateLocalMediaFile(fileAtLimit, 300, cloudCapabilities)).toEqual({ ok: true })
    expect(validateLocalMediaFile(fileOverLimit, 300, cloudCapabilities)).toEqual({
      ok: false,
      code: 'too_large',
      message: 'CloudBase 演示入口限制 20 MB；请先压缩视频再上传',
    })
    expect(effectiveLocalUploadMaxBytes(capabilities)).toBe(10)
  })
})
