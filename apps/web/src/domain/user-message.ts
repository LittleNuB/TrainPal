/** Only explicitly authored product messages may be displayed as error text. */
export class UserMessageError extends Error {}

export const userMessage = (error: unknown, fallback: string): string =>
  error instanceof UserMessageError ? error.message : fallback
