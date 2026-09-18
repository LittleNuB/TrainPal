import { readdirSync, readFileSync } from 'node:fs'
import { relative, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const sourceRoot = resolve(process.cwd(), 'src')

interface StyleSource {
  file: string
  css: string
}

const collectStyleSources = (directory: string): StyleSource[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = resolve(directory, entry.name)
    if (entry.isDirectory()) return collectStyleSources(fullPath)
    if (!entry.name.endsWith('.vue') && !entry.name.endsWith('.css')) return []

    const source = readFileSync(fullPath, 'utf8')
    if (entry.name.endsWith('.css')) {
      return [{ file: relative(sourceRoot, fullPath), css: source }]
    }

    return [...source.matchAll(/<style(?:\s[^>]*)?>([\s\S]*?)<\/style>/g)].map((match) => ({
      file: relative(sourceRoot, fullPath),
      css: match[1] ?? '',
    }))
  })

const styleSources = collectStyleSources(sourceRoot)

const relativeLuminance = (hex: string): number => {
  const channels = hex.match(/[0-9a-f]{2}/gi)?.map((channel) => Number.parseInt(channel, 16) / 255)
  if (!channels || channels.length !== 3) throw new Error(`Invalid RGB color: ${hex}`)
  const [red, green, blue] = channels.map((channel) => (
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  )) as [number, number, number]
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue
}

const contrastRatio = (foreground: string, background: string): number => {
  const foregroundLuminance = relativeLuminance(foreground)
  const backgroundLuminance = relativeLuminance(background)
  return (
    (Math.max(foregroundLuminance, backgroundLuminance) + 0.05)
    / (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
  )
}

describe('web experience design contract', () => {
  it('keeps explicit auxiliary type at 11px or larger', () => {
    const violations = styleSources.flatMap(({ file, css }) => {
      const declarations = [
        ...css.matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)px/g),
        ...css.matchAll(/font\s*:[^;{}]*?\s(\d+(?:\.\d+)?)px(?=[/\s;])/g),
      ]

      return declarations
        .map((match) => Number(match[1]))
        .filter((size) => size < 11)
        .map((size) => `${file}: ${size}px`)
    })

    expect(violations).toEqual([])
  })

  it('keeps explicitly sized interactive targets at least 44px', () => {
    const violations = styleSources.flatMap(({ file, css }) =>
      [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].flatMap((match) => {
        const selector = match[1] ?? ''
        const declarations = match[2] ?? ''
        if (!/(?:^|[\s>+~,])(button|a|input|select)(?=[:.[#\s>+~,]|$)/.test(selector)) return []

        return [...declarations.matchAll(/min-(?:width|height)\s*:\s*(\d+(?:\.\d+)?)px/g)]
          .map((dimension) => Number(dimension[1]))
          .filter((dimension) => dimension < 44)
          .map((dimension) => `${file}: ${selector.trim()} uses ${dimension}px`)
      }),
    )

    expect(violations).toEqual([])
  })

  it('publishes the approved C palette with a light training stage', () => {
    const baseStyles = readFileSync(resolve(sourceRoot, 'styles/base.css'), 'utf8')

    expect(baseStyles).toMatch(/--tp-canvas\s*:\s*#F8F7F2/i)
    expect(baseStyles).toMatch(/--tp-surface\s*:\s*#FFFEFA/i)
    expect(baseStyles).toMatch(/--tp-primary\s*:\s*#B96342/i)
    expect(baseStyles).toMatch(/--tp-secondary\s*:\s*#586C49/i)
    expect(baseStyles).toMatch(/--tp-training-canvas\s*:\s*var\(--tp-canvas\)/i)
    expect(baseStyles).toMatch(/--tp-focus\s*:\s*#2459D6/i)
  })

  it('keeps muted text readable on every light semantic surface', () => {
    const baseStyles = readFileSync(resolve(sourceRoot, 'styles/base.css'), 'utf8')
    const muted = baseStyles.match(/--tp-muted\s*:\s*(#[0-9a-f]{6})/i)?.[1]

    expect(muted).toBeDefined()
    for (const background of ['#F8F7F2', '#FFFEFA', '#EBEDE1', '#FFF9ED', '#EAE6DC']) {
      expect(contrastRatio(muted ?? '', background), `${muted} on ${background}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('keeps the keyboard focus ring at 2px with a 3px offset', () => {
    const baseStyles = readFileSync(resolve(sourceRoot, 'styles/base.css'), 'utf8')
    const focusRule = baseStyles.match(
      /button:focus-visible,\s*a:focus-visible,\s*select:focus-visible,\s*input:focus-visible\s*\{([^}]*)\}/,
    )

    expect(focusRule).not.toBeNull()
    expect(focusRule?.[1]).toMatch(/outline\s*:\s*2px\s+solid\s+var\(--tp-focus\)/)
    expect(focusRule?.[1]).toMatch(/outline-offset\s*:\s*3px/)
  })

  it('keeps training reference video visible without cropping', () => {
    const trainingView = readFileSync(resolve(sourceRoot, 'views/TrainingView.vue'), 'utf8')
    const videoRule = trainingView.match(/\.media-stage\s+video\s*\{([^}]*)\}/)

    expect(videoRule).not.toBeNull()
    expect(videoRule?.[1]).toMatch(/object-fit\s*:\s*contain/)
  })
})
