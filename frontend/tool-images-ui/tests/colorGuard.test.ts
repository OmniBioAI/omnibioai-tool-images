import fs from 'node:fs'
import path from 'node:path'
import { expect, test } from 'vitest'

const EXPLICIT_FILE_EXCEPTIONS = new Set([
  'src/assets/react.svg',
  'src/vendor/omnibioai-design-tokens.css',
  'public/vite.svg',
])
const COLOR = /#[\da-f]{3,8}\b|\brgba?\s*\(/i

function sourceFiles(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const absolute = path.join(directory, entry.name)
    return entry.isDirectory() ? sourceFiles(absolute) : [absolute]
  })
}

test('runtime source has no literal colors outside the single token-mapping block', () => {
  const root = process.cwd()
  const files = [path.join(root, 'index.html'), ...sourceFiles(path.join(root, 'src'))]
    .filter(file => /\.(?:css|html|ts|tsx|svg)$/.test(file))
    .filter(file => !EXPLICIT_FILE_EXCEPTIONS.has(path.relative(root, file)))
  const failures: string[] = []
  for (const file of files) {
    let source = fs.readFileSync(file, 'utf8')
    if (path.relative(root, file) === 'src/index.css') {
      const blocks = source.match(/\/\* color-token-mapping:start \*\/[\s\S]*?\/\* color-token-mapping:end \*\//g)
      expect(blocks).toHaveLength(1)
      source = source.replace(blocks![0], '')
    }
    if (COLOR.test(source)) failures.push(path.relative(root, file))
  }
  expect(failures).toEqual([])
})

test('vendored package is pristine 1.1.0 without tooling or nested lock', () => {
  const vendor = path.resolve(process.cwd(), '../../vendor/omnibioai-design-tokens')
  const metadata = JSON.parse(fs.readFileSync(path.join(vendor, 'package.json'), 'utf8'))
  expect(metadata.version).toBe('1.1.0')
  expect(metadata.scripts).toBeUndefined()
  expect(metadata.devDependencies).toBeUndefined()
  expect(fs.existsSync(path.join(vendor, 'package-lock.json'))).toBe(false)
  expect(fs.readFileSync(path.join(process.cwd(), 'src/vendor/omnibioai-design-tokens.css'), 'utf8'))
    .toBe(fs.readFileSync(path.join(vendor, 'tokens.css'), 'utf8'))
})

test('nginx CSP applies frame ancestry policy on all responses', () => {
  const dockerfile = fs.readFileSync(path.resolve(process.cwd(), '../../Dockerfile'), 'utf8')
  expect(dockerfile.match(/add_header Content-Security-Policy "frame-ancestors '\\''self'\\'' https:\/\/webstudio\.omnibioai\.org" always;/g)).toHaveLength(1)
})
