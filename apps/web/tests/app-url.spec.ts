import { describe, expect, it } from 'vitest'
import { appUrl } from '@/api/url'

describe('deployment URLs', () => {
  it('preserves root development and prefixes API, SSE and registered assets once', () => {
    for (const path of ['/api/v1/capabilities', '/api/v1/analysis-runs/id/events', '/trainpal/pets/zen/idle/01.webp']) {
      expect(appUrl(path, '/')).toBe(path)
      expect(appUrl(path, '/trainpal/')).toBe(`/trainpal${path}`)
    }
    expect(() => appUrl('//other.example/path', '/trainpal/')).toThrow()
  })
})
