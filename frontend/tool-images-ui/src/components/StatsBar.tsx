import type { Tool } from '../types'

interface Props { tools: Tool[] }

export default function StatsBar({ tools }: Props) {
  const built = tools.filter(t => t.status === 'built').length
  const reused = tools.filter(t => t.status === 'reused').length
  const license = tools.filter(t => t.status === 'license').length
  const missing = tools.filter(t => t.status === 'missing').length

  return (
    <div style={{
      display: 'flex',
      gap: 1,
      background: 'var(--border)',
      borderBottom: '1px solid var(--border)',
    }}>
      <StatCard label="Built & ready" value={built} color="var(--color-success)" />
      <StatCard label="Reused SIF" value={reused} color="var(--color-info)" />
      <StatCard label="Needs license" value={license} color="var(--color-warning)" />
      <StatCard label="Missing / broken" value={missing} color="var(--color-danger)" />
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{
      flex: 1,
      background: 'var(--bg1)',
      padding: '10px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 2,
    }}>
      <span style={{ fontSize: 22, fontWeight: 600, color, lineHeight: 1 }}>{value}</span>
      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{label}</span>
    </div>
  )
}
