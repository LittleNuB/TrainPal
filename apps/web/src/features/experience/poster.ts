import { UserMessageError } from '@/domain/user-message'
import {
  coachFrameUrls,
  resolveCoachEventAction,
  type CoachStyleId,
} from '@/domain/coach'
import type { TrainingRecord } from '@/domain/training'

export interface CompletionPosterInput {
  outcome: TrainingRecord['outcome']
  planName: string
  trainingDurationSeconds: number
  caloriesKcal: number
  completedActionCount: number
  coachStyleId: CoachStyleId | null
}

export interface CompletionPosterModel {
  title: '训练完成'
  planName: string
  durationLabel: string
  caloriesLabel: string
  actionCountLabel: string
}

export interface PosterDrawingContext {
  fillStyle: string | CanvasGradient | CanvasPattern
  font: string
  textAlign: CanvasTextAlign
  textBaseline: CanvasTextBaseline
  fillRect(x: number, y: number, width: number, height: number): void
  fillText(text: string, x: number, y: number, maxWidth?: number): void
  drawImage(image: CanvasImageSource, dx: number, dy: number, dWidth: number, dHeight: number): void
}

export interface PosterSurface {
  context: PosterDrawingContext
  exportPng(): Promise<Blob>
}

export interface PosterRuntime {
  createSurface(width: number, height: number): PosterSurface
  loadImage(url: string): Promise<CanvasImageSource>
}

export interface PosterDeliveryRuntime {
  canShare(data: ShareData): boolean
  share(data: ShareData): Promise<void>
  download(file: File, filename: string): void
}

export type PosterDeliveryResult = 'shared' | 'downloaded' | 'cancelled'

const POSTER_WIDTH = 1_080
const POSTER_HEIGHT = 1_920

function asNonNegativeInteger(value: number): number {
  if (!Number.isFinite(value) || value < 0) throw new UserMessageError('海报数据不完整')
  return Math.round(value)
}

export function buildCompletionPosterModel(input: CompletionPosterInput): CompletionPosterModel {
  if (input.outcome !== 'completed') throw new UserMessageError('只有完整训练可以生成完成海报')

  const planName = input.planName.trim()
  if (!planName) throw new UserMessageError('海报数据不完整')

  const durationSeconds = asNonNegativeInteger(input.trainingDurationSeconds)
  const caloriesKcal = asNonNegativeInteger(input.caloriesKcal)
  const completedActionCount = asNonNegativeInteger(input.completedActionCount)

  return {
    title: '训练完成',
    planName,
    durationLabel: `${Math.round(durationSeconds / 60)} 分钟`,
    caloriesLabel: `约 ${caloriesKcal} 千卡`,
    actionCountLabel: `完成 ${completedActionCount} 个动作`,
  }
}

function createBrowserRuntime(): PosterRuntime {
  return {
    createSurface(width, height) {
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const context = canvas.getContext('2d')
      if (!context) throw new UserMessageError('当前浏览器无法生成海报')

      return {
        context,
        exportPng: () =>
          new Promise<Blob>((resolve, reject) => {
            canvas.toBlob((blob) => {
              if (blob) resolve(blob)
              else reject(new UserMessageError('海报生成失败，请重试'))
            }, 'image/png')
          }),
      }
    },
    loadImage(url) {
      return new Promise<CanvasImageSource>((resolve, reject) => {
        const image = new Image()
        image.onload = () => resolve(image)
        image.onerror = () => reject(new UserMessageError('TrainPal 小猫素材加载失败，请重试'))
        image.src = url
      })
    },
  }
}

function drawPoster(
  context: PosterDrawingContext,
  model: CompletionPosterModel,
  pet: CanvasImageSource | null,
): void {
  context.fillStyle = '#F3EFE5'
  context.fillRect(0, 0, POSTER_WIDTH, POSTER_HEIGHT)
  context.fillStyle = '#FFFDF8'
  context.fillRect(72, 84, 936, 1_752)
  context.fillStyle = '#A5BA63'
  context.fillRect(72, 84, 18, 1_752)
  context.fillStyle = '#D94B2B'
  context.fillRect(90, 84, 918, 18)

  context.textAlign = 'left'
  context.textBaseline = 'alphabetic'
  context.fillStyle = '#D94B2B'
  context.font = '700 88px "Arial Narrow", "Microsoft YaHei", sans-serif'
  context.fillText(model.title, 144, 310, 780)
  context.fillStyle = '#1C2822'
  context.font = '700 74px "Microsoft YaHei", sans-serif'
  context.fillText(model.planName, 144, 470, 792)

  context.fillStyle = '#6C746E'
  context.font = '500 38px "Microsoft YaHei", sans-serif'
  context.fillText('本次训练', 144, 670)
  context.fillStyle = '#1C2822'
  context.font = '700 64px "Arial Narrow", "Microsoft YaHei", sans-serif'
  context.fillText(model.durationLabel, 144, 760)
  context.fillText(model.caloriesLabel, 144, 890)
  context.fillText(model.actionCountLabel, 144, 1_020)

  if (pet) context.drawImage(pet, 570, 1_110, 360, 405)
  context.fillStyle = '#6C746E'
  context.font = '500 34px "Microsoft YaHei", sans-serif'
  context.fillText('TrainPal · 你的专属训练伙伴', 144, 1_690)
  context.fillStyle = '#D94B2B'
  context.font = '700 34px "Microsoft YaHei", sans-serif'
  context.fillText('继续找动作，下一练见', 144, 1_765)
}

export async function renderCompletionPoster(
  input: CompletionPosterInput,
  runtime: PosterRuntime = createBrowserRuntime(),
): Promise<Blob> {
  const model = buildCompletionPosterModel(input)
  const surface = runtime.createSurface(POSTER_WIDTH, POSTER_HEIGHT)
  const completedAction = input.coachStyleId
    ? resolveCoachEventAction(input.coachStyleId, 'session_completed')
    : null
  const pet = input.coachStyleId && completedAction
    ? await runtime.loadImage(coachFrameUrls(input.coachStyleId, completedAction)[0]!)
    : null
  drawPoster(surface.context, model, pet)
  return surface.exportPng()
}

function posterFilename(planName: string): string {
  const withoutControlCharacters = [...planName.trim()]
    .map((character) => (character.charCodeAt(0) < 32 ? '-' : character))
    .join('')
  const safePlanName = withoutControlCharacters
    .replace(/[<>:"/\\|?*]/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 48)
  return `TrainPal-${safePlanName || '训练完成'}.png`
}

function createBrowserDeliveryRuntime(): PosterDeliveryRuntime {
  return {
    canShare(data) {
      return typeof navigator.share === 'function' &&
        (typeof navigator.canShare !== 'function' || navigator.canShare(data))
    },
    share(data) {
      return navigator.share(data)
    },
    download(file, filename) {
      const url = URL.createObjectURL(file)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 0)
    },
  }
}

export async function deliverCompletionPoster(
  blob: Blob,
  planName: string,
  runtime: PosterDeliveryRuntime = createBrowserDeliveryRuntime(),
): Promise<PosterDeliveryResult> {
  const filename = posterFilename(planName)
  const file = new File([blob], filename, { type: 'image/png' })
  const shareData: ShareData = {
    title: 'TrainPal 训练完成海报',
    files: [file],
  }

  if (runtime.canShare(shareData)) {
    try {
      await runtime.share(shareData)
      return 'shared'
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return 'cancelled'
    }
  }

  runtime.download(file, filename)
  return 'downloaded'
}

export function downloadCompletionPoster(
  blob: Blob,
  planName: string,
  runtime: PosterDeliveryRuntime = createBrowserDeliveryRuntime(),
): void {
  const filename = posterFilename(planName)
  runtime.download(new File([blob], filename, { type: 'image/png' }), filename)
}
