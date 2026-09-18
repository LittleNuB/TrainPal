export interface CoachPoint { x: number; y: number }
export interface CoachRect extends CoachPoint { width: number; height: number }

export const overlaps = (a: CoachRect, b: CoachRect): boolean => (
  a.x < b.x + b.width && a.x + a.width > b.x
  && a.y < b.y + b.height && a.y + a.height > b.y
)

/** Find the closest visible position that leaves task controls unobstructed. */
export function placeCoach(
  desired: CoachPoint,
  bounds: CoachRect,
  size: { width: number; height: number },
  obstacles: CoachRect[],
  snapToEdge: boolean,
): CoachPoint | null {
  const minX = bounds.x + 8
  const minY = bounds.y + 8
  const maxX = bounds.x + bounds.width - size.width - 8
  const maxY = bounds.y + bounds.height - size.height - 8
  if (maxX < minX || maxY < minY) return null
  const clampX = (x: number) => Math.max(minX, Math.min(maxX, x))
  const clampY = (y: number) => Math.max(minY, Math.min(maxY, y))
  const xs = snapToEdge ? [minX, maxX] : [clampX(desired.x), minX, maxX]
  const ys = [clampY(desired.y), minY, maxY]
  for (const obstacle of obstacles) {
    if (!snapToEdge) xs.push(clampX(obstacle.x - size.width - 8), clampX(obstacle.x + obstacle.width + 8))
    ys.push(clampY(obstacle.y - size.height - 8), clampY(obstacle.y + obstacle.height + 8))
  }
  return xs.flatMap((x) => ys.map((y) => ({ x, y })))
    .filter((point) => obstacles.every((obstacle) => !overlaps({ ...point, ...size }, obstacle)))
    .sort((a, b) => Math.hypot(a.x - desired.x, a.y - desired.y) - Math.hypot(b.x - desired.x, b.y - desired.y))[0] ?? null
}
