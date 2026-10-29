import { Box, Database, Layers, Cpu } from 'lucide-react'

interface Props {
  active: string
}

const NAV_ITEMS = [
  { id: 'tool-images', icon: Box, label: 'Tool Images' },
  { id: 'model-registry', icon: Database, label: 'Model Registry' },
  { id: 'rag', icon: Layers, label: 'RAG' },
  { id: 'tes', icon: Cpu, label: 'TES' },
]

export default function IconNav({ active }: Props) {
  return (
    <nav className="app-icon-nav" style={{
      width: 52,
      minWidth: 52,
      background: 'var(--color-bg-elevated)',
      borderRight: '1px solid var(--border)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      paddingTop: 16,
      gap: 4,
      zIndex: 10,
    }}>
      <div style={{
        width: 32,
        height: 32,
        borderRadius: 8,
        background: 'var(--accent)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: 20,
        flexShrink: 0,
      }}>
        <span style={{ color: 'var(--on-accent)', fontWeight: 700, fontSize: 14 }}>O</span>
      </div>

      {NAV_ITEMS.map(({ id, icon: Icon, label }) => {
        const isActive = id === active
        return (
          <button
            key={id}
            title={label}
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: isActive ? 'var(--accent-dim)' : 'transparent',
              border: isActive ? '1px solid var(--accent-border)' : '1px solid transparent',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: isActive ? 'var(--accent)' : 'var(--text-muted)',
              transition: 'all 0.15s',
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => {
              if (!isActive) {
                (e.currentTarget as HTMLElement).style.background = 'var(--bg2)'
                ;(e.currentTarget as HTMLElement).style.color = 'var(--text-dim)'
              }
            }}
            onMouseLeave={(e) => {
              if (!isActive) {
                (e.currentTarget as HTMLElement).style.background = 'transparent'
                ;(e.currentTarget as HTMLElement).style.color = 'var(--text-muted)'
              }
            }}
          >
            <Icon size={16} />
          </button>
        )
      })}
    </nav>
  )
}
