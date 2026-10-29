import { useState, useEffect } from 'react'
import { X, RefreshCw, FileText, Copy, Server, Terminal } from 'lucide-react'
import type { Tool } from '../types'
import { fetchBuildLog, fetchDockerfile, streamBuild } from '../api'

interface Props {
  tool: Tool
  onClose: () => void
  onShowDockerfile: (content: string, tool: string) => void
}

export default function DetailPanel({ tool, onClose, onShowDockerfile }: Props) {
  const [buildLog, setBuildLog] = useState<string>('')
  const [liveLog, setLiveLog] = useState<string | null>(null)
  const [building, setBuilding] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setBuildLog('')
    setLiveLog(null)
    fetchBuildLog(tool.name).then(setBuildLog).catch(() => {})
  }, [tool.name])

  const handleRebuild = () => {
    if (building) return
    setLiveLog('')
    setBuilding(true)
    streamBuild(
      tool.name,
      (line) => setLiveLog(prev => (prev ?? '') + line + '\n'),
      (success) => {
        setBuilding(false)
        setLiveLog(prev => (prev ?? '') + `\n[${success ? 'SUCCESS' : 'FAILED'}]`)
      }
    )
  }

  const handleViewDockerfile = async () => {
    const content = await fetchDockerfile(tool.name).catch(() => '')
    onShowDockerfile(content, tool.name)
  }

  const handleCopySif = () => {
    const cmd = `singularity run ${tool.sif_path ?? `sif/${tool.sif_filename}`}`
    navigator.clipboard.writeText(cmd).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const displayLog = liveLog ?? buildLog

  return (
    <aside style={{
      width: 230,
      minWidth: 230,
      borderLeft: '1px solid var(--border)',
      background: 'var(--bg1)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 12px',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{tool.name}</span>
        <button onClick={onClose} style={{ background: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 2 }}>
          <X size={14} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* Image info */}
        <Section label="Image">
          <InfoRow label="SIF" value={tool.sif_filename} />
          <InfoRow label="Size" value={tool.sif_size_mb ? `${tool.sif_size_mb} MB` : '—'} />
          <InfoRow label="Status" value={tool.status} valueColor={statusColor(tool.status)} />
          <InfoRow label="Updated" value={tool.updated_at ? new Date(tool.updated_at).toLocaleString() : '—'} />
          <InfoRow label="Category" value={tool.category} />
        </Section>

        {/* TES registration */}
        <Section label="TES">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <Server size={12} color="var(--text-muted)" />
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Not registered</span>
          </div>
          <button style={{
            width: '100%',
            padding: '5px 8px',
            background: 'var(--bg3)',
            border: '1px solid var(--border)',
            borderRadius: 5,
            color: 'var(--text-dim)',
            fontSize: 11,
            fontFamily: 'var(--font)',
            cursor: 'pointer',
          }}>
            Register in TES
          </button>
        </Section>

        {/* Build log */}
        <Section label={liveLog !== null ? 'Live build log' : 'Last build log'}>
          {building && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              <Terminal size={11} color="var(--accent)" />
              <span style={{ fontSize: 10, color: 'var(--accent)' }}>Building…</span>
            </div>
          )}
          <div style={{
            background: 'var(--color-bg)',
            border: '1px solid var(--border)',
            borderRadius: 5,
            padding: 8,
            maxHeight: 180,
            overflowY: 'auto',
            fontFamily: 'var(--font)',
            fontSize: 10,
            color: 'var(--color-success)',
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}>
            {displayLog || <span style={{ color: 'var(--text-muted)' }}>No log available</span>}
          </div>
        </Section>
      </div>

      {/* Action buttons */}
      <div style={{
        borderTop: '1px solid var(--border)',
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}>
        {tool.status !== 'license' && (
          <PanelBtn icon={<RefreshCw size={11} />} label={building ? 'Building…' : 'Rebuild'} accent disabled={building} onClick={handleRebuild} />
        )}
        <PanelBtn icon={<FileText size={11} />} label="View Dockerfile" onClick={handleViewDockerfile} />
        <PanelBtn icon={<Copy size={11} />} label={copied ? 'Copied!' : 'Copy singularity run'} onClick={handleCopySif} />
      </div>
    </aside>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
        {label}
      </div>
      {children}
    </div>
  )
}

function InfoRow({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, gap: 8 }}>
      <span style={{ fontSize: 11, color: 'var(--text-muted)', flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 11, color: valueColor ?? 'var(--text-dim)', textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>
    </div>
  )
}

function PanelBtn({ icon, label, accent, disabled, onClick }: { icon: React.ReactNode; label: string; accent?: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 10px',
        borderRadius: 5,
        background: accent ? 'var(--accent)' : 'var(--bg3)',
        border: `1px solid ${accent ? 'transparent' : 'var(--border)'}`,
        color: accent ? 'var(--on-accent)' : 'var(--text-dim)',
        fontFamily: 'var(--font)',
        fontSize: 11,
        fontWeight: accent ? 600 : 400,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.6 : 1,
      }}
    >
      {icon}{label}
    </button>
  )
}

function statusColor(s: string): string {
  const map: Record<string, string> = { built: 'var(--color-success)', reused: 'var(--color-info)', license: 'var(--color-warning)', missing: 'var(--color-danger)' }
  return map[s] ?? 'var(--text-dim)'
}
