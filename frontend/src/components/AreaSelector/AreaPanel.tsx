import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { Area } from '../../types'
import MapView, { isContiguous, MAX_CELLS } from './MapView'

type Mode = 'pincode' | 'name' | 'grid_cells'

const isMobile = () => window.matchMedia('(max-width: 899px)').matches

export default function AreaPanel({ onStarted }: { onStarted: (reportId: number) => void }) {
  const [mode, setMode] = useState<Mode>('name')
  const [text, setText] = useState('')
  const [pins, setPins] = useState<{ pincode: string; label: string }[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [area, setArea] = useState<Area | null>(null)
  const [busy, setBusy] = useState<'find' | 'run' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [tooFar, setTooFar] = useState(false)

  useEffect(() => {
    api.pincodes().then(setPins).catch(() => setPins([]))
  }, [])

  const ids = [...selected]
  const contiguous = mode !== 'grid_cells' || ids.length === 0 || isContiguous(ids)
  const gridProblem =
    mode !== 'grid_cells'
      ? null
      : ids.length === 0
        ? 'Tap grid cells on the map to select an area.'
        : ids.length > MAX_CELLS
          ? `Too many cells (${ids.length}). Maximum is ${MAX_CELLS}.`
          : !contiguous
            ? 'Selected cells must touch edge to edge. Add cells between them or remove stray ones.'
            : null

  const toggle = (id: string) => {
    setArea(null)
    setSelected((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  const switchMode = (m: Mode) => {
    setMode(m)
    setArea(null)
    setError(null)
    setCollapsed(false)
  }

  const find = async () => {
    setError(null)
    setBusy('find')
    try {
      const a = await api.resolve(mode, mode === 'grid_cells' ? ids : text.trim())
      setArea(a)
      if (isMobile()) setCollapsed(false)
    } catch (e) {
      setArea(null)
      setError((e as ApiError).message)
    } finally {
      setBusy(null)
    }
  }

  const run = async () => {
    if (!area) return
    setError(null)
    setBusy('run')
    try {
      const r = await api.generate(area.area_id)
      onStarted(r.report_id)
    } catch (e) {
      setError((e as ApiError).message)
      setBusy(null)
    }
  }

  const canFind = mode === 'grid_cells' ? !gridProblem : text.trim().length >= (mode === 'pincode' ? 6 : 3)

  return (
    <div className="main">
      <MapView
        area={area}
        gridMode={mode === 'grid_cells'}
        selected={selected}
        onToggleCell={toggle}
        bottomPad={isMobile() ? 320 : 24}
        onGridZoomState={setTooFar}
      />
      {mode === 'grid_cells' && tooFar && <div className="zoom-note">Zoom in to see the 500 m grid</div>}
      <section className={`sheet${collapsed ? ' collapsed' : ''}`} aria-label="Choose an area">
        <button className="sheet-handle" onClick={() => setCollapsed((c) => !c)} aria-expanded={!collapsed}>
          {collapsed ? 'Show area picker' : 'Hide'}
        </button>
        <div className="sheet-body stack">
          <div>
            <h1 style={{ fontSize: 18, marginBottom: 2 }}>Where should we look?</h1>
            <p className="hint">Pick any part of Chennai and get a fitness report without leaving the office.</p>
          </div>
          <div className="seg" role="group" aria-label="How to pick an area">
            <button aria-pressed={mode === 'name'} onClick={() => switchMode('name')}>Locality</button>
            <button aria-pressed={mode === 'pincode'} onClick={() => switchMode('pincode')}>Pincode</button>
            <button aria-pressed={mode === 'grid_cells'} onClick={() => switchMode('grid_cells')}>Map cells</button>
          </div>

          {mode !== 'grid_cells' ? (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                if (canFind && !busy) find()
              }}
            >
              <label className="field-label" htmlFor="q">
                {mode === 'pincode' ? 'Pincode (6 digits)' : 'Locality name'}
              </label>
              <input
                id="q"
                className="input"
                value={text}
                onChange={(e) => {
                  setText(e.target.value)
                  setArea(null)
                }}
                inputMode={mode === 'pincode' ? 'numeric' : 'text'}
                maxLength={mode === 'pincode' ? 6 : 60}
                placeholder={mode === 'pincode' ? 'e.g. 600042' : 'e.g. Velachery'}
                list={mode === 'pincode' ? 'pincode-list' : undefined}
                autoComplete="off"
              />
              {mode === 'pincode' && (
                <datalist id="pincode-list">
                  {pins.map((p) => (
                    <option key={p.pincode} value={p.pincode}>{p.label}</option>
                  ))}
                </datalist>
              )}
              <button className="btn block" style={{ marginTop: 12 }} disabled={!canFind || busy !== null} type="submit">
                {busy === 'find' ? <span className="loader" /> : null} Find area
              </button>
            </form>
          ) : (
            <div className="stack">
              <div className="msg info">
                <b>{ids.length}</b> of {MAX_CELLS} cells selected
                {ids.length > 0 && (
                  <button className="btn ghost sm" style={{ marginLeft: 10 }} onClick={() => { setSelected(new Set()); setArea(null) }}>
                    Clear
                  </button>
                )}
              </div>
              {gridProblem && <p className="hint">{gridProblem}</p>}
              <button className="btn block" disabled={!canFind || busy !== null} onClick={find}>
                {busy === 'find' ? <span className="loader" /> : null} Use these cells
              </button>
            </div>
          )}

          {error && <div className="msg err" role="alert">{error}</div>}

          {area && (
            <div className="area-card stack">
              <h3>{area.name}</h3>
              <div className="chips">
                <span className="chip">{area.area_km2} km²</span>
                <span className={`chip${area.boundary_quality === 'exact' ? '' : ' warn'}`}>
                  {area.boundary_quality === 'exact' ? 'Exact boundary' : 'Approximate boundary'}
                </span>
              </div>
              {area.note && <p className="hint">{area.note}</p>}
              <button className="btn yellow block" onClick={run} disabled={busy !== null}>
                {busy === 'run' ? <span className="loader" /> : null} Run virtual analysis
              </button>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
