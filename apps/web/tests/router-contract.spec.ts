import { describe, expect, it } from 'vitest'

import { createTrainPalRoutes } from '@/router'

describe('TrainPal route experience contract', () => {
  it('keeps only home, training and mine in the top-level navigation shell', () => {
    const routes = createTrainPalRoutes(false)
    const topLevel = routes
      .filter((route) => route.meta?.showBottomNav)
      .map((route) => route.path)

    expect(topLevel).toEqual(['/', '/train', '/mine'])
    expect(routes.find((route) => route.path === '/training')?.meta?.theme).toBe('training')
    expect(routes.find((route) => route.path === '/analysis')?.meta?.shell).toBe('immersive')
  })

  it('exposes each recoverable task everywhere it stays relevant', () => {
    const routes = createTrainPalRoutes(false)
    const withAnalysisEntry = routes
      .filter((route) => route.meta?.showAnalysisTask)
      .map((route) => route.path)
    const withTrainingEntry = routes
      .filter((route) => route.meta?.showTrainingTask)
      .map((route) => route.path)

    expect(withAnalysisEntry).toEqual([
      '/',
      '/plan',
      '/personalize',
      '/train',
      '/result/:recordId',
      '/mine',
    ])
    expect(withTrainingEntry).toEqual([
      '/',
      '/analysis',
      '/plan',
      '/personalize',
      '/result/:recordId',
      '/mine',
    ])
    // The active workout stays immersive; task entry remains available after leaving it.
    expect(routes.find((route) => route.path === '/training')?.meta?.showAnalysisTask).toBe(false)
  })

  it('does not register the design gallery in production route records', () => {
    expect(createTrainPalRoutes(false).some((route) => route.path === '/__design/trainpal')).toBe(false)
    expect(createTrainPalRoutes(true).some((route) => route.path === '/__design/trainpal')).toBe(true)
    expect(createTrainPalRoutes(false).some((route) => route.path === '/__design/trainpal/pets')).toBe(false)
    expect(createTrainPalRoutes(true).some((route) => route.path === '/__design/trainpal/pets')).toBe(true)
  })
})
