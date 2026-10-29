import type { Tool } from '../types'

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

interface Props {
  tools: Tool[]
  activeCategory: string | null
  onCategorySelect: (cat: string | null) => void
}

export default function Sidebar({ tools, activeCategory, onCategorySelect }: Props) {
  const cats = tools.reduce<Record<string, { total: number; built: number }>>((acc, t) => {
    const c = t.category
    if (!acc[c]) acc[c] = { total: 0, built: 0 }
    acc[c].total++
    if (t.status === 'built' || t.status === 'reused') acc[c].built++
    return acc
  }, {})

  const totalSizeMb = tools.reduce((s, t) => s + (t.sif_size_mb ?? 0), 0)
  const totalBuilt = tools.filter(t => t.status === 'built').length
  const totalReused = tools.filter(t => t.status === 'reused').length
  const totalLicense = tools.filter(t => t.status === 'license').length
  const totalMissing = tools.filter(t => t.status === 'missing').length
  const sizeLabel = totalSizeMb > 1024
    ? `${(totalSizeMb / 1024).toFixed(1)} GB`
    : `${totalSizeMb.toFixed(0)} MB`

  return (
    <aside className="app-sidebar" style={{
      width: 188,
      minWidth: 188,
      background: 'var(--bg1)',
      borderRight: '1px solid var(--border)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <div style={{ padding: '14px 12px 8px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
          Categories
        </div>
        <button
          onClick={() => onCategorySelect(null)}
          style={{
            width: '100%',
            textAlign: 'left',
            padding: '4px 6px',
            borderRadius: 5,
            background: activeCategory === null ? 'var(--accent-dim)' : 'transparent',
            color: activeCategory === null ? 'var(--accent)' : 'var(--text-dim)',
            fontSize: 12,
            fontFamily: 'var(--font)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            cursor: 'pointer',
            border: 'none',
          }}
        >
          <span>All tools</span>
          <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{tools.length}</span>
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
        {Object.entries(cats).sort((a, b) => a[0].localeCompare(b[0])).map(([cat, { total }]) => {
          const color = CATEGORY_COLORS[cat] ?? 'var(--category-other)'
          const isActive = activeCategory === cat
          return (
            <button
              key={cat}
              onClick={() => onCategorySelect(isActive ? null : cat)}
              style={{
                width: '100%',
                textAlign: 'left',
                padding: '5px 12px',
                background: isActive ? 'var(--accent-dim)' : 'transparent',
                border: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                cursor: 'pointer',
                fontFamily: 'var(--font)',
                fontSize: 11.5,
                color: isActive ? 'var(--text)' : 'var(--text-dim)',
              }}
            >
              <span style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: color,
                flexShrink: 0,
              }} />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cat}</span>
              <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{total}</span>
            </button>
          )
        })}
      </div>

      <div style={{
        borderTop: '1px solid var(--border)',
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 5,
      }}>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>
          Storage
        </div>
        <StorageStat label="Total size" value={sizeLabel} color="var(--accent)" />
        <StorageStat label="Built" value={String(totalBuilt)} color="var(--color-success)" />
        <StorageStat label="Reused" value={String(totalReused)} color="var(--color-info)" />
        <StorageStat label="License" value={String(totalLicense)} color="var(--color-warning)" />
        <StorageStat label="Missing" value={String(totalMissing)} color="var(--color-danger)" />
      </div>
    </aside>
  )
}

function StorageStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{label}</span>
      <span style={{ color, fontSize: 11, fontWeight: 500 }}>{value}</span>
    </div>
  )
}
