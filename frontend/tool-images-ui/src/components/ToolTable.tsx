import { RefreshCw, FileText, Key } from 'lucide-react'
import type { Tool, ToolStatus } from '../types'

const CATEGORY_COLORS: Record<string, string> = {
  'Bio Core': 'var(--category-bio)',
  'Variant Analysis': 'var(--category-variant)',
  'RNA-seq': 'var(--category-rna)',
  'Metagenomics': 'var(--category-metagenomics)',
  'Epigenomics': 'var(--category-epigenomics)',
  'Single-cell': 'var(--category-single-cell)',
  'Proteomics': 'var(--category-proteomics)',
  'Structural Biology': 'var(--category-structural)',
  'ML / AI': 'var(--category-ml)',
  'More Bioinformatics': 'var(--category-more)',
  'Population Genetics': 'var(--category-population)',
  'Other': 'var(--category-other)',
}

const STATUS_STYLE: Record<ToolStatus, { bg: string; color: string; label: string }> = {
  built: { bg: 'var(--color-bg-elevated)', color: 'var(--color-success)', label: 'built' },
  reused: { bg: 'var(--color-bg-elevated)', color: 'var(--color-info)', label: 'reused' },
  license: { bg: 'var(--color-bg-elevated)', color: 'var(--color-warning)', label: 'license' },
  missing: { bg: 'var(--color-bg-elevated)', color: 'var(--color-danger)', label: 'missing' },
}

interface Props {
  tools: Tool[]
  selected: Tool | null
  onSelect: (t: Tool) => void
  onRebuild: (t: Tool) => void
  onViewDockerfile: (t: Tool) => void
  maxSizeMb: number
}

export default function ToolTable({ tools, selected, onSelect, onRebuild, onViewDockerfile, maxSizeMb }: Props) {
  return (
    <div style={{ flex: 1, overflowY: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
        <colgroup>
          <col style={{ width: '22%' }} />
          <col style={{ width: '16%' }} />
          <col style={{ width: '13%' }} />
          <col style={{ width: '16%' }} />
          <col style={{ width: '16%' }} />
          <col style={{ width: '17%' }} />
        </colgroup>
        <thead>
          <tr style={{ background: 'var(--bg2)', position: 'sticky', top: 0, zIndex: 2 }}>
            {['Tool', 'Category', 'Status', 'Size', 'Updated', 'Actions'].map(h => (
              <th key={h} style={{
                textAlign: 'left',
                padding: '8px 12px',
                fontSize: 10,
                fontWeight: 500,
                color: 'var(--text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
                borderBottom: '1px solid var(--border)',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tools.map(tool => {
            const isSelected = selected?.name === tool.name
            const ss = STATUS_STYLE[tool.status]
            const catColor = CATEGORY_COLORS[tool.category] ?? 'var(--category-other)'
            const sizeRatio = maxSizeMb > 0 && tool.sif_size_mb ? tool.sif_size_mb / maxSizeMb : 0
            const updatedLabel = tool.updated_at
              ? new Date(tool.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
              : '—'

            return (
              <tr
                key={tool.name}
                onClick={() => onSelect(tool)}
                style={{
                  background: isSelected ? 'var(--accent-dim)' : 'transparent',
                  borderBottom: '1px solid var(--border)',
                  cursor: 'pointer',
                  transition: 'background 0.1s',
                }}
                onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'var(--bg2)' }}
                onMouseLeave={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
              >
                <td style={{ padding: '8px 12px' }}>
                  <div style={{ fontWeight: 500, color: 'var(--text)', fontSize: 12 }}>{tool.name}</div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {tool.sif_filename}
                  </div>
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: catColor, flexShrink: 0 }} />
                    <span style={{ fontSize: 11, color: catColor, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tool.category}</span>
                  </span>
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: ss.bg,
                    color: ss.color,
                    fontSize: 11,
                    fontWeight: 500,
                  }}>{ss.label}</span>
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                    {tool.sif_size_mb ? `${tool.sif_size_mb} MB` : '—'}
                  </div>
                  {sizeRatio > 0 && (
                    <div style={{ marginTop: 3, height: 3, borderRadius: 2, background: 'var(--bg3)', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${Math.min(sizeRatio * 100, 100)}%`, background: 'var(--accent)', borderRadius: 2 }} />
                    </div>
                  )}
                </td>
                <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-muted)' }}>
                  {updatedLabel}
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {tool.status !== 'license' && (
                      <ActionBtn
                        icon={<RefreshCw size={11} />}
                        label="Rebuild"
                        color="var(--accent)"
                        onClick={e => { e.stopPropagation(); onRebuild(tool) }}
                      />
                    )}
                    <ActionBtn
                      icon={<FileText size={11} />}
                      label="Dockerfile"
                      color="var(--text-dim)"
                      onClick={e => { e.stopPropagation(); onViewDockerfile(tool) }}
                    />
                    {tool.status === 'license' && (
                      <ActionBtn
                        icon={<Key size={11} />}
                        label="Get license"
                        color="var(--color-warning)"
                        onClick={e => e.stopPropagation()}
                      />
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ActionBtn({ icon, label, color, onClick }: { icon: React.ReactNode; label: string; color: string; onClick: (e: React.MouseEvent) => void }) {
  return (
    <button
      title={label}
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        padding: '3px 7px',
        borderRadius: 4,
        background: 'var(--bg3)',
        border: '1px solid var(--border)',
        color,
        fontSize: 10,
        fontFamily: 'var(--font)',
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {icon}{label}
    </button>
  )
}
