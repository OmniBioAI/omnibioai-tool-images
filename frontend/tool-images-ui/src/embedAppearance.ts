export const EMBED_READY_EVENT = 'omnibioai.embed.ready.v1'
export const EMBED_THEME_EVENT = 'omnibioai.theme.set.v1'

type Appearance = {
  theme: 'light' | 'dark'
  accent: 'teal' | 'blue' | 'purple' | 'orange'
  density: 'comfortable' | 'compact'
  motion: 'system' | 'reduce'
}

const DEFAULTS: Appearance = Object.freeze({ theme: 'dark', accent: 'teal', density: 'comfortable', motion: 'system' })
const ALLOWED = Object.freeze({
  theme: new Set(['light', 'dark']),
  accent: new Set(['teal', 'blue', 'purple', 'orange']),
  density: new Set(['comfortable', 'compact']),
  motion: new Set(['system', 'reduce']),
})
const PRODUCTION_STUDIO_ORIGIN = 'https://webstudio.omnibioai.org'
const MESSAGE_KEYS = ['accent', 'density', 'motion', 'theme', 'type']
const DEV_STUDIO_ORIGIN = import.meta.env.VITE_STUDIO_DEV_ORIGIN || ''

function configuredStudioOrigin(value: string): string | null {
  if (!value) return null
  try {
    const parsed = new URL(value)
    if (!/^https?:$/.test(parsed.protocol) || parsed.origin !== value || parsed.pathname !== '/' ||
        parsed.search || parsed.hash || parsed.username || parsed.password) return null
    return parsed.origin
  } catch {
    return null
  }
}

export function normalizeAppearance(values: Record<string, unknown> = {}, systemTheme = 'dark'): Appearance {
  return {
    theme: (ALLOWED.theme.has(values.theme as string) ? values.theme : (ALLOWED.theme.has(systemTheme) ? systemTheme : DEFAULTS.theme)) as Appearance['theme'],
    accent: (ALLOWED.accent.has(values.accent as string) ? values.accent : DEFAULTS.accent) as Appearance['accent'],
    density: (ALLOWED.density.has(values.density as string) ? values.density : DEFAULTS.density) as Appearance['density'],
    motion: (ALLOWED.motion.has(values.motion as string) ? values.motion : DEFAULTS.motion) as Appearance['motion'],
  }
}

export function applyAppearance(root: HTMLElement, values: Record<string, unknown>): Appearance {
  const appearance = normalizeAppearance(values)
  root.dataset.theme = appearance.theme
  root.dataset.accent = appearance.accent
  root.dataset.density = appearance.density
  root.dataset.motion = appearance.motion
  return appearance
}

export function parseThemeMessage(data: unknown): Appearance | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null
  const message = data as Record<string, unknown>
  const keys = Object.keys(message).sort()
  const expected = [...MESSAGE_KEYS].sort()
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  if (message.type !== EMBED_THEME_EVENT || !Object.entries(ALLOWED).every(([key, values]) => values.has(message[key] as string))) return null
  return message as Appearance & { type: string }
}

export function connectAppearanceMessages(
  win: Window = window,
  doc: Document = document,
  devStudioOrigin: string = DEV_STUDIO_ORIGIN,
): () => void {
  if (win.parent === win || !doc.referrer) return () => {}
  let parentOrigin: string
  try { parentOrigin = new URL(doc.referrer).origin } catch { return () => {} }

  const allowedOrigins = new Set([PRODUCTION_STUDIO_ORIGIN])
  const validatedDevOrigin = configuredStudioOrigin(devStudioOrigin)
  if (validatedDevOrigin) allowedOrigins.add(validatedDevOrigin)
  if (!allowedOrigins.has(parentOrigin)) return () => {}

  const onMessage = (event: MessageEvent) => {
    if (event.origin !== parentOrigin || event.source !== win.parent) return
    const appearance = parseThemeMessage(event.data)
    if (appearance) applyAppearance(doc.documentElement, appearance)
  }
  win.addEventListener('message', onMessage)
  win.parent.postMessage({ type: EMBED_READY_EVENT }, parentOrigin)
  return () => win.removeEventListener('message', onMessage)
}
