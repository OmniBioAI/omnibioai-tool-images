import { useState, useEffect, useMemo } from 'react'
import { X } from 'lucide-react'
import IconNav from './components/IconNav'
import Sidebar from './components/Sidebar'
import TopBar from './components/TopBar'
import StatsBar from './components/StatsBar'
import ToolTable from './components/ToolTable'
import DetailPanel from './components/DetailPanel'
import { fetchTools, streamBuildAll } from './api'
import type { Tool } from './types'

export default function App() {
  const [tools, setTools] = useState<Tool[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [selected, setSelected] = useState<Tool | null>(null)
  const [buildingAll, setBuildingAll] = useState(false)
  const [dockerfileModal, setDockerfileModal] = useState<{ content: string; tool: string } | null>(null)

  useEffect(() => {
    fetchTools()
      .then(setTools)
      .catch(() => setError('Cannot reach API'))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    let ts = tools
    if (activeCategory) ts = ts.filter(t => t.category === activeCategory)
    if (search.trim()) {
      const q = search.toLowerCase()
      ts = ts.filter(t => t.name.toLowerCase().includes(q) || t.category.toLowerCase().includes(q))
    }
    return ts
  }, [tools, activeCategory, search])

  const maxSizeMb = useMemo(() => Math.max(...tools.map(t => t.sif_size_mb ?? 0), 1), [tools])
  const totalSizeMb = useMemo(() => tools.reduce((s, t) => s + (t.sif_size_mb ?? 0), 0), [tools])

  const handleBuildAll = () => {
    if (buildingAll) return
    setBuildingAll(true)
    streamBuildAll(
      () => {},
      () => {
        setBuildingAll(false)
        fetchTools().then(setTools).catch(() => {})
      }
    )
  }

  const handleRebuild = (tool: Tool) => {
    setSelected(tool)
  }

  const handleViewDockerfile = (_tool: Tool) => {
    setSelected(_tool)
  }

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--bg)' }}>
      <IconNav active="tool-images" />
      <Sidebar tools={tools} activeCategory={activeCategory} onCategorySelect={setActiveCategory} />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <TopBar
          search={search}
          onSearch={setSearch}
          onBuildAll={handleBuildAll}
          building={buildingAll}
          totalSizeMb={totalSizeMb}
        />
        <StatsBar tools={tools} />

        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          {loading ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
              Loading tools…
            </div>
          ) : error ? (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--color-danger)' }}>
              <span>{error}</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Run: python api/server.py</span>
            </div>
          ) : (
            <ToolTable
              tools={filtered}
              selected={selected}
              onSelect={setSelected}
              onRebuild={handleRebuild}
              onViewDockerfile={handleViewDockerfile}
              maxSizeMb={maxSizeMb}
            />
          )}

          {selected && (
            <DetailPanel
              tool={selected}
              onClose={() => setSelected(null)}
              onShowDockerfile={(content, tool) => setDockerfileModal({ content, tool })}
            />
          )}
        </div>
      </div>

      {dockerfileModal && (
        <div style={{
          position: 'fixed', inset: 0, background: 'color-mix(in srgb, var(--color-bg) 70%, transparent)', zIndex: 100,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} onClick={() => setDockerfileModal(null)}>
          <div style={{
            background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 10,
            width: '60vw', maxWidth: 800, maxHeight: '80vh',
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
          }} onClick={e => e.stopPropagation()}>
            <div style={{
              padding: '10px 16px', borderBottom: '1px solid var(--border)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                Dockerfile.{dockerfileModal.tool}
              </span>
              <button onClick={() => setDockerfileModal(null)} style={{ background: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <pre style={{
              flex: 1, overflowY: 'auto', padding: 16,
              fontFamily: 'var(--font)', fontSize: 12, lineHeight: 1.7,
              color: 'var(--color-text-soft)', margin: 0, background: 'var(--color-bg)',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {dockerfileModal.content}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
