/** Resolve app-owned URLs for both root development and the portfolio mount. */
export function appUrl(path: string, base = import.meta.env.BASE_URL): string {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('App URL must be an absolute path')
  return base.replace(/\/$/, '') + path
}
