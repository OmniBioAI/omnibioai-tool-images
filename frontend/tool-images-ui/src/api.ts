import type { Tool } from './types'

const BASE = ''

export async function fetchTools(): Promise<Tool[]> {
  const res = await fetch(`${BASE}/v1/tools`)
  if (!res.ok) throw new Error('Failed to fetch tools')
  return res.json()
}

export async function fetchDockerfile(tool: string): Promise<string> {
  const res = await fetch(`${BASE}/v1/tools/${tool}/dockerfile`)
  if (!res.ok) throw new Error('Dockerfile not found')
  return res.text()
}

export async function fetchBuildLog(tool: string): Promise<string> {
  const res = await fetch(`${BASE}/v1/tools/${tool}/log`)
  if (!res.ok) return ''
  return res.text()
}

export function streamBuild(
  tool: string,
  onLine: (line: string) => void,
  onDone: (success: boolean) => void
): () => void {
  const url = `${BASE}/v1/build/${encodeURIComponent(tool)}`
  const ctrl = new AbortController()

  fetch(url, { method: 'POST', signal: ctrl.signal })
    .then(async (res) => {
      if (!res.body) return onDone(false)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (line.startsWith('data:')) onLine(line.slice(5).trim())
          if (line.startsWith('event: done')) onDone(true)
          if (line.startsWith('event: error')) onDone(false)
        }
      }
      onDone(true)
    })
    .catch(() => onDone(false))

  return () => ctrl.abort()
}

export function streamBuildAll(
  onLine: (line: string) => void,
  onDone: (success: boolean) => void
): () => void {
  const url = `${BASE}/v1/build-all`
  const ctrl = new AbortController()

  fetch(url, { method: 'POST', signal: ctrl.signal })
    .then(async (res) => {
      if (!res.body) return onDone(false)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (line.startsWith('data:')) onLine(line.slice(5).trim())
          if (line.startsWith('event: done')) onDone(true)
          if (line.startsWith('event: error')) onDone(false)
        }
      }
      onDone(true)
    })
    .catch(() => onDone(false))

  return () => ctrl.abort()
}
