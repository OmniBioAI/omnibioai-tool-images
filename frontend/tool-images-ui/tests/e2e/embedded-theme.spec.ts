import { expect, test, type FrameLocator, type Page } from '@playwright/test'

const fixtures = [
  { name: 'fastqc', category: 'Bio Core', sif_path: '/sif/fastqc_arm64.sif', sif_filename: 'fastqc_arm64.sif', sif_exists: true, sif_size_mb: 42, status: 'built', dockerfile: 'Dockerfile.fastqc', updated_at: '2026-10-28T12:00:00' },
  { name: 'cellranger', category: 'Single-cell', sif_path: null, sif_filename: 'cellranger_arm64.sif', sif_exists: false, sif_size_mb: null, status: 'license', dockerfile: 'Dockerfile.cellranger', updated_at: null },
  { name: 'missing-tool', category: 'Other', sif_path: null, sif_filename: 'missing-tool_arm64.sif', sif_exists: false, sif_size_mb: null, status: 'missing', dockerfile: 'Dockerfile.missing-tool', updated_at: null },
]

async function mockApi(page: Page) {
  await page.route('**/v1/tools', route => route.fulfill({ json: fixtures }))
  await page.route('**/v1/tools/**/log', route => route.fulfill({ body: '' }))
}

async function openEmbedded(page: Page, query: string, viewport: { width: number; height: number }) {
  await page.setViewportSize(viewport)
  await mockApi(page)
  await page.setContent(`<style>html,body{margin:0}</style><iframe title="Embedded Tool Images" src="http://127.0.0.1:4179/_svc/toolimages/?${query}" style="display:block;width:100vw;height:${viewport.height}px;border:0"></iframe>`)
  const frame = page.frameLocator('iframe')
  await expect(frame.getByText('fastqc', { exact: true }).first()).toBeVisible()
  await expect(frame.locator('html')).toHaveAttribute('data-embedded', 'true')
  await expect(frame.locator('.app-top-bar')).toHaveCSS('display', 'none')
  return frame
}

async function auditTextContrast(frame: FrameLocator) {
  return frame.locator('body').evaluate(body => {
    const parse = (value: string) => (value.match(/[\d.]+/g) || []).map(Number)
    const composite = (front: number[], back: number[]) => front.length > 3 && front[3] < 1
      ? front.slice(0, 3).map((channel, index) => channel * front[3] + back[index] * (1 - front[3]))
      : front.slice(0, 3)
    const luminance = ([r, g, b]: number[]) => {
      const channels = [r, g, b].map(value => { const s = value / 255; return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4 })
      return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]
    }
    const contrast = (a: number[], b: number[]) => { const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (light + .05) / (dark + .05) }
    return [body, ...body.querySelectorAll('*')].flatMap(element => {
      const hasText = [...element.childNodes].some(node => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())
      const style = getComputedStyle(element)
      if (!hasText || style.display === 'none' || style.visibility === 'hidden') return []
      const layers: Element[] = []
      for (let layer: Element | null = element; layer && layer !== document.documentElement; layer = layer.parentElement) layers.push(layer)
      let backdrop = parse(getComputedStyle(document.body).backgroundColor).slice(0, 3)
      for (const layer of layers.reverse()) {
        const color = parse(getComputedStyle(layer).backgroundColor)
        if (color.length >= 3 && !(color[0] === 0 && color[1] === 0 && color[2] === 0 && color[3] === 0)) backdrop = composite(color, backdrop)
      }
      const ratio = contrast(parse(style.color), backdrop)
      return ratio < 4.5 ? [{ text: element.textContent?.trim().slice(0, 40), ratio }] : []
    })
  })
}

test('embedded light + purple uses purple active state with AA text at 1440px', async ({ page }) => {
  const frame = await openEmbedded(page, 'obai_theme=light&obai_accent=purple&obai_density=comfortable&obai_motion=system', { width: 1440, height: 900 })
  await frame.getByText('fastqc', { exact: true }).first().click()
  await page.waitForTimeout(200)
  const colors = await frame.locator('html').evaluate(() => {
    const selected = document.querySelector('tbody tr') as HTMLElement
    const probe = document.createElement('span')
    probe.style.background = 'var(--color-accent-dim)'
    document.body.appendChild(probe)
    const result = {
      accent: getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim(),
      expectedSelected: getComputedStyle(probe).backgroundColor,
      selected: getComputedStyle(selected).backgroundColor,
    }
    probe.remove()
    return result
  })
  expect(colors.accent).toBe('#7030a0')
  expect(colors.selected).toBe(colors.expectedSelected)
  expect(await auditTextContrast(frame)).toEqual([])
  await page.locator('iframe').screenshot({ path: '/tmp/tool-images-light-purple-1440.png' })
})

test('embedded dark + teal keeps AA text at 390px', async ({ page }) => {
  const frame = await openEmbedded(page, 'obai_theme=dark&obai_accent=teal&obai_density=compact&obai_motion=reduce', { width: 390, height: 844 })
  expect(await auditTextContrast(frame)).toEqual([])
  await page.locator('iframe').screenshot({ path: '/tmp/tool-images-dark-teal-390.png' })
})

test('standalone keeps its top bar', async ({ page }) => {
  await mockApi(page)
  await page.goto('?obai_theme=dark&obai_accent=teal')
  await expect(page.locator('html')).toHaveAttribute('data-embedded', 'false')
  await expect(page.locator('.app-top-bar')).toBeVisible()
})
