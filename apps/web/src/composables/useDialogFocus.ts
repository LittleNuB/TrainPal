import { nextTick, type Ref } from 'vue'

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

const focusableElements = (dialog: HTMLElement): HTMLElement[] =>
  [...dialog.querySelectorAll<HTMLElement>(focusableSelector)]
    .filter((element) => element.getAttribute('aria-hidden') !== 'true' && !element.closest('dialog:not([open])'))

export const useDialogFocus = (dialog: Ref<HTMLElement | null>) => {
  let returnTarget: HTMLElement | null = null

  const activate = async (trigger?: HTMLElement | null): Promise<void> => {
    returnTarget = trigger
      ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    await nextTick()
    const current = dialog.value
    if (!current) return
    const preferred = current.querySelector<HTMLElement>('[data-dialog-initial-focus]')
    ;(preferred ?? focusableElements(current)[0] ?? current).focus()
  }

  const deactivate = async (): Promise<void> => {
    const target = returnTarget
    returnTarget = null
    await nextTick()
    if (target?.isConnected) target.focus()
  }

  const onKeydown = (event: KeyboardEvent, close: () => void): void => {
    const current = dialog.value
    if (!current) return
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
      return
    }
    if (event.key !== 'Tab') return

    const elements = focusableElements(current)
    if (!elements.length) {
      event.preventDefault()
      current.focus()
      return
    }
    const first = elements[0]!
    const last = elements[elements.length - 1]!
    const active = document.activeElement
    if (event.shiftKey && (active === first || !current.contains(active))) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && (active === last || !current.contains(active))) {
      event.preventDefault()
      first.focus()
    }
  }

  return { activate, deactivate, onKeydown }
}
