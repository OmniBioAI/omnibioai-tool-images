import { Search, Layers, Play } from 'lucide-react'

interface Props {
  search: string
  onSearch: (v: string) => void
  onBuildAll: () => void
  building: boolean
  totalSizeMb: number
}

export default function TopBar({ search, onSearch, onBuildAll, building, totalSizeMb }: Props) {
  const sizeLabel = totalSizeMb > 1024
    ? `${(totalSizeMb / 1024).toFixed(1)} GB`
    : `${totalSizeMb.toFixed(0)} MB`

  return (
    <div className="app-top-bar" style={{
      height: 52,
      minHeight: 52,
      background: 'var(--bg1)',
      borderBottom: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 16px',
      gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Layers size={15} color="var(--accent)" />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', letterSpacing: '0.01em' }}>
          Tool Images
        </span>
      </div>

      <div className="app-top-bar-search" style={{ flex: 1, maxWidth: 320, marginLeft: 16, position: 'relative' }}>
        <Search size={13} style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
        <input
          type="text"
          placeholder="Search tools..."
          value={search}
          onChange={e => onSearch(e.target.value)}
          style={{
            width: '100%',
            background: 'var(--bg2)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '5px 10px 5px 28px',
            color: 'var(--text)',
            fontSize: 12,
            outline: 'none',
          }}
          onFocus={e => (e.target.style.borderColor = 'var(--accent-border)')}
          onBlur={e => (e.target.style.borderColor = 'var(--border)')}
        />
      </div>

      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Badge label="ARM64" color="var(--color-purple)" />
        <Badge label={sizeLabel} color="var(--text-muted)" />

        <button
          onClick={onBuildAll}
          disabled={building}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 12px',
            borderRadius: 6,
            background: building ? 'var(--bg3)' : 'var(--accent)',
            color: building ? 'var(--text-muted)' : 'var(--on-accent)',
            fontSize: 12,
            fontWeight: 600,
            fontFamily: 'var(--font)',
            border: 'none',
            cursor: building ? 'not-allowed' : 'pointer',
            transition: 'all 0.15s',
          }}
        >
          <Play size={12} />
          {building ? 'Building…' : 'Build All'}
        </button>
      </div>
    </div>
  )
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span className="app-top-bar-badge" style={{
      padding: '2px 7px',
      borderRadius: 4,
      background: 'var(--bg3)',
      border: '1px solid var(--border)',
      color,
      fontSize: 11,
      fontWeight: 500,
    }}>
      {label}
    </span>
  )
}
