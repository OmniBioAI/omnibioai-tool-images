import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { describe, expect, test } from 'vitest'
import {
  connectAppearanceMessages,
  EMBED_READY_EVENT,
  EMBED_THEME_EVENT,
  parseThemeMessage,
} from '../src/embedAppearance'

const html = fs.readFileSync(path.resolve(process.cwd(), 'index.html'), 'utf8')
const bootstrap = html.match(/<script>\s*([\s\S]*?)\s*<\/script>/)?.[1]

function embedded(referrer = 'https://webstudio.omnibioai.org/workbench') {
  const listeners = new Map<string, EventListener>()
  const sent: Array<[unknown, string]> = []
  const parent = { postMessage: (message: unknown, origin: string) => sent.push([message, origin]) }
  const win = {
    parent,
    addEventListener: (type: string, handler: EventListener) => listeners.set(type, handler),
    removeEventListener: (type: string) => listeners.delete(type),
  }
  const root = { dataset: {}, style: { cssText: 'untouched' }, innerHTML: 'untouched' }
  return { win, doc: { referrer, documentElement: root }, parent, root, listeners, sent }
}

describe('embedded appearance protocol', () => {
  test('pre-paint bootstrap accepts bounded values and removes private parameters', () => {
    expect(bootstrap).toBeTruthy()
    expect(html.indexOf('<script>')).toBeLessThan(html.indexOf('<link'))
    const location = {
      pathname: '/_svc/toolimages/',
      search: '?obai_theme=%3Csvg%3E&obai_accent=purple&obai_density=compact&obai_motion=reduce&obai_owner=x&token=x&author=Jane&username=reader',
      hash: '#go',
    }
    let replaced = ''
    const root = { dataset: {}, style: { cssText: 'untouched' }, innerHTML: 'untouched' }
    const windowObject: Record<string, unknown> = {
      location,
      matchMedia: () => ({ matches: false }),
      history: { state: null, replaceState: (_state: unknown, _title: string, url: string) => { replaced = url } },
    }
    windowObject.parent = windowObject
    vm.runInNewContext(bootstrap!, { window: windowObject, document: { documentElement: root }, URLSearchParams, Object, Set, Array })
    expect(root.dataset).toEqual({ theme: 'light', accent: 'purple', density: 'compact', motion: 'reduce', embedded: 'false' })
    const clean = new URL(replaced, 'https://tools.example')
    expect(clean.searchParams.has('obai_owner')).toBe(false)
    expect(clean.searchParams.has('token')).toBe(false)
    expect(clean.searchParams.get('author')).toBe('Jane')
    expect(clean.searchParams.get('username')).toBe('reader')
    expect(clean.hash).toBe('#go')
    expect(root.style.cssText).toBe('untouched')
    expect(root.innerHTML).toBe('untouched')
  })

  test('requires exact production origin, parent source, exact keys, and allowed values', () => {
    const fixture = embedded()
    const stop = connectAppearanceMessages(fixture.win as unknown as Window, fixture.doc as unknown as Document, '')
    expect(fixture.sent).toEqual([[{ type: EMBED_READY_EVENT }, 'https://webstudio.omnibioai.org']])
    const listener = fixture.listeners.get('message')!
    const message = { type: EMBED_THEME_EVENT, theme: 'light', accent: 'blue', density: 'compact', motion: 'reduce' }
    const deliver = (origin: string, source: unknown, data: unknown) => listener({ origin, source, data } as unknown as Event)
    deliver('https://evil.example', fixture.parent, message)
    deliver('https://webstudio.omnibioai.org', {}, message)
    deliver('https://webstudio.omnibioai.org', fixture.parent, { ...message, token: 'x' })
    deliver('https://webstudio.omnibioai.org', fixture.parent, { ...message, username: 'reader' })
    deliver('https://webstudio.omnibioai.org', fixture.parent, { ...message, accent: 'javascript:alert(1)' })
    expect(fixture.root.dataset).toEqual({})
    expect(parseThemeMessage({ ...message, extra: true })).toBeNull()
    deliver('https://webstudio.omnibioai.org', fixture.parent, message)
    expect(fixture.root.dataset).toEqual({ theme: 'light', accent: 'blue', density: 'compact', motion: 'reduce' })
    expect(fixture.root.style.cssText).toBe('untouched')
    expect(fixture.root.innerHTML).toBe('untouched')
    stop()
    expect(fixture.listeners.has('message')).toBe(false)
  })

  test('development origin defaults empty and must be explicitly exact', () => {
    const noDefault = embedded('http://localhost:5174/')
    connectAppearanceMessages(noDefault.win as unknown as Window, noDefault.doc as unknown as Document, '')
    expect(noDefault.sent).toEqual([])
    const configured = embedded('http://localhost:5174/')
    connectAppearanceMessages(configured.win as unknown as Window, configured.doc as unknown as Document, 'http://localhost:5174')
    expect(configured.sent).toEqual([[{ type: EMBED_READY_EVENT }, 'http://localhost:5174']])
    const spoofed = embedded('http://localhost:5174/')
    connectAppearanceMessages(spoofed.win as unknown as Window, spoofed.doc as unknown as Document, 'http://localhost:5174.attacker.example')
    expect(spoofed.sent).toEqual([])
  })
})
