import { describe, expect, it } from 'vitest'
import { overlaps, placeCoach } from '@/domain/coach-position'

describe('coach placement', () => {
  const bounds = { x: 0, y: 0, width: 390, height: 844 }
  const size = { width: 112, height: 132 }
  it('snaps to a mobile edge without covering the primary action', () => {
    const action = { x: 0, y: 740, width: 390, height: 104 }
    const point = placeCoach({ x: 200, y: 800 }, bounds, size, [action], true)!
    expect(point.x).toBe(270)
    expect(overlaps({ ...point, ...size }, action)).toBe(false)
    expect(point.y).toBeGreaterThanOrEqual(8)
  })
  it('keeps free desktop positions and recovers after viewport shrink', () => {
    expect(placeCoach({ x: 500, y: 300 }, { ...bounds, width: 1440 }, size, [], false)).toEqual({ x: 500, y: 300 })
    const point = placeCoach({ x: 1300, y: 900 }, bounds, size, [], false)!
    expect(point.x + size.width).toBeLessThanOrEqual(382)
    expect(point.y + size.height).toBeLessThanOrEqual(836)
  })
  it('uses the visual viewport and yields when no safe area remains', () => {
    expect(placeCoach({ x: 0, y: 0 }, { x: 0, y: 200, width: 390, height: 300 }, size, [], true)?.y).toBe(208)
    expect(placeCoach({ x: 0, y: 0 }, bounds, size, [bounds], true)).toBeNull()
  })
})
