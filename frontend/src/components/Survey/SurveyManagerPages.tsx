import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useCallback, useEffect, useState } from 'react'
import { GeoJSON, MapContainer, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { api, ApiError } from '../../api/client'
import type { Insights, SplitPreview, Study, StudyRow } from '../../types'
import { fmtTime } from '../common/format'
import InsightsView from './InsightsView'

const COLORS = ['#782B90', '#E0A800', '#2E86AB', '#C2185B', '#3A7D44', '#8E5A2B', '#5C6BC0', '#D9480F', '#0B7285', '#7B1FA2', '#6D6D00', '#AD1457']
const STATUS_TEXT: Record<string, string> = { REQUESTED: 'Needs split', IN_PROGRESS: 'In progress', COMPLETED: 'Completed' }

function Fit({ geom }: { geom: GeoJSON.GeoJsonObject }) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds(L.geoJSON(geom).getBounds(), { padding: [20, 20] })
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [geom, map])
  return null
}

export function StudyList({ onOpen }: { onOpen: (id: number) => void }) {
  const [items, setItems] = useState<StudyRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [f, setF] = useState('')
  const load = useCallback(() => api.studies().then((r) => { setItems(r); setError(null) }).catch((e: ApiError) => setError(e.message)), [])
  useEffect(() => { load() }, [load])
  const rows = (items ?? []).filter((s) => !f || (f === 'split' ? s.needs_split : s.status === f))
  return (
    <div className="scroll-view">
      <div className="page stack">
        <div>
          <h1 style={{ fontSize: 22 }}>Catchment studies</h1>
          <p className="hint">Requests from the BD Manager. Split each one into work units, assign executives and review the results.</p>
        </div>
        <div className="filter-row" role="group" aria-label="Filter">
          {[['', 'All'], ['split', 'Needs split'], ['IN_PROGRESS', 'In progress'], ['COMPLETED', 'Completed']].map(([v, l]) => (
            <button key={v} className={`pill${f === v ? ' on' : ''}`} onClick={() => setF(v)} aria-pressed={f === v}>{l}</button>
          ))}
        </div>
        {error && <div className="msg err">{error}</div>}
        {!items && !error && <div className="empty"><span className="loader" /></div>}
        {items && rows.length === 0 && <div className="empty card"><h2>Nothing here</h2><p>New requests from the BD Manager appear in this list.</p></div>}
        {rows.length > 0 && (
          <section className="card">
            {rows.map((s) => (
              <button key={s.study_id} className="prop-row study-row" onClick={() => onOpen(s.study_id)}>
                <div className="pr-photo">{s.kind === 'property' ? 'Property' : 'Area'}</div>
                <div className="pr-main">
                  <b>{s.label}</b>
                  <div className="hint">{s.sublabel ?? ''} · requested {fmtTime(s.requested_at)}</div>
                  <div className="chips">
                    <span className={`chip${s.needs_split ? ' yellow' : ''}`}>{s.needs_split ? 'Needs split' : STATUS_TEXT[s.status]}</span>
                    {s.reused && <span className="chip">reused survey</span>}
                    {!!s.units_total && <span className="chip">{s.units_completed}/{s.units_total} units done</span>}
                  </div>
                </div>
                <div className="mini-score" aria-hidden>›</div>
              </button>
            ))}
          </section>
        )}
      </div>
    </div>
  )
}

export function StudyDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const [s, setS] = useState<Study | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [count, setCount] = useState<number | undefined>(undefined)
  const [prev, setPrev] = useState<SplitPreview | null>(null)
  const [who, setWho] = useState<string[]>([])
  const [review, setReview] = useState<Insights | null>(null)

  const load = useCallback(() => api.study(id).then((r) => { setS(r); setError(null) }).catch((e: ApiError) => setError(e.message)), [id])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!s || s.status === 'COMPLETED') return
    const t = window.setInterval(load, 8000) // executives are working: keep progress fresh
    return () => window.clearInterval(t)
  }, [s, load])

  const doPreview = async (n?: number) => {
    setBusy('preview'); setError(null)
    try {
      const p = await api.splitPreview(id, n)
      setPrev(p); setCount(p.meta.unit_count); setWho(p.units.map((u) => u.suggested_assignee))
    } catch (e) { setError((e as ApiError).message) } finally { setBusy(null) }
  }
  const confirm = async () => {
    if (!prev) return
    setBusy('confirm'); setError(null)
    try { await api.createUnits(id, prev.meta.unit_count, who); setPrev(null); await load() } catch (e) { setError((e as ApiError).message) } finally { setBusy(null) }
  }
  const doReview = async () => {
    setBusy('review'); setError(null)
    try { setReview(await api.insightsPreview(id)) } catch (e) { setError((e as ApiError).message) } finally { setBusy(null) }
  }
  const complete = async () => {
    setBusy('complete'); setError(null)
    try { await api.completeStudy(id); setReview(null); await load() } catch (e) { setError((e as ApiError).message) } finally { setBusy(null) }
  }
  const regen = async () => {
    setBusy('regen'); setError(null)
    try { await api.regenerateInsights(id); await load() } catch (e) { setError((e as ApiError).message) } finally { setBusy(null) }
  }

  if (!s) return <div className="scroll-view"><div className="page stack"><button className="btn ghost sm" onClick={onBack}>← Studies</button>{error ? <div className="msg err">{error}</div> : <div className="empty"><span className="loader" /></div>}</div></div>
  const units = s.units ?? []
  const shown = prev ? prev.units.map((u, i) => ({ code: u.unit_code, geometry: u.geometry, color: COLORS[i % COLORS.length], text: `${u.unit_code} · ${who[i] ? who[i].split(':')[1] : ''}` }))
    : units.map((u, i) => ({ code: u.unit_code, geometry: u.geometry!, color: COLORS[i % COLORS.length], text: `${u.unit_code} · ${u.assigned_to_name.split(' ')[0]} · ${u.status.toLowerCase().replace('_', ' ')}` }))

  return (
    <div className="scroll-view">
      <div className="page stack">
        <button className="btn ghost sm" onClick={onBack}>← Studies</button>
        <div>
          <h1 style={{ fontSize: 22 }}>{s.label}</h1>
          <p className="hint">Study #{s.study_id} · {s.kind === 'property' ? 'around a property' : 'whole area'} · requested {fmtTime(s.requested_at)}</p>
          <div className="chips" style={{ marginTop: 8 }}><span className="chip yellow">{STATUS_TEXT[s.status]}</span>{s.reused && <span className="chip">reused survey</span>}</div>
        </div>
        {error && <div className="msg err" role="alert">{error}</div>}
        {s.reused && <div className="msg info">{s.reuse_reason} Nothing to split or survey for this request.</div>}

        <div className="report-map" style={{ height: 300 }}>
          <MapContainer center={[13.08, 80.27]} zoom={14} maxZoom={21} style={{ height: '100%' }}>
            <TileLayer maxZoom={21} maxNativeZoom={19} attribution="&copy; OpenStreetMap contributors" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
            <GeoJSON data={s.study_geometry} style={{ color: '#782B90', weight: 3, fillOpacity: 0.03, dashArray: '6 6' }} />
            {shown.map((u) => (
              <GeoJSON key={`${u.code}-${u.color}-${who.join()}`} data={u.geometry} style={{ color: u.color, weight: 2, fillColor: u.color, fillOpacity: 0.25 }}>
                <Tooltip sticky>{u.text}</Tooltip>
              </GeoJSON>
            ))}
            <Fit geom={s.study_geometry} />
          </MapContainer>
        </div>

        {s.can_split && !prev && (
          <section className="card stack">
            <h2>Split the catchment</h2>
            <p className="hint">The catchment is divided into non-overlapping work units balanced by lane length, shops and amenities and estimated households (not by lane count alone).</p>
            <button className="btn yellow block" disabled={busy !== null} onClick={() => doPreview()}>{busy === 'preview' ? <span className="loader" /> : null} Preview the split</button>
          </section>
        )}

        {prev && (
          <section className="card stack">
            <h2>Preview: {prev.units.length} work units</h2>
            <p className="hint">Balance: the heaviest unit is {prev.meta.balance_ratio}× the lightest ({prev.meta.study_area_km2} km²).
              {prev.meta.flags.includes('split_by_area_only') ? ' Road data was unavailable, so this split is by equal area only.' : ''} Households are estimates.</p>
            <div className="two-col">
              <button className="btn ghost sm" disabled={busy !== null || (count ?? 1) <= 1} onClick={() => doPreview((count ?? 2) - 1)}>− Fewer units</button>
              <button className="btn ghost sm" disabled={busy !== null || (count ?? 12) >= 12} onClick={() => doPreview((count ?? 1) + 1)}>+ More units</button>
            </div>
            <div className="table-scroll">
              <table className="cmp">
                <thead><tr><th>Unit</th><th>Workload</th><th>Lanes</th><th>Target</th><th>Assign to</th></tr></thead>
                <tbody>
                  {prev.units.map((u, i) => (
                    <tr key={u.unit_code}>
                      <td><span className="swatch" style={{ background: COLORS[i % COLORS.length] }} /> <b>{u.unit_code}</b></td>
                      <td className="num">{u.workload.points}<div className="hint">{u.estimated_distance_m != null ? `${(u.estimated_distance_m / 1000).toFixed(1)} km lanes` : 'by area'}</div></td>
                      <td>{u.workload.lanes.slice(0, 3).map((l) => l.name).join(', ') || '-'}</td>
                      <td className="num">{u.target_capture_count}</td>
                      <td>
                        <select className="input" style={{ minHeight: 40 }} value={who[i] ?? ''} onChange={(e) => setWho((w) => w.map((x, j) => (j === i ? e.target.value : x)))}>
                          {prev.executives.map((x) => <option key={x.id} value={x.id}>{x.name.split(' (')[0]}{x.open_points ? ` (${x.open_points} pts open)` : ''}</option>)}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="two-col">
              <button className="btn ghost" onClick={() => setPrev(null)}>Cancel</button>
              <button className="btn yellow" disabled={busy !== null || who.some((w) => !w)} onClick={confirm}>{busy === 'confirm' ? <span className="loader" /> : null} Confirm and assign</button>
            </div>
          </section>
        )}

        {units.length > 0 && s.progress && (
          <section className="card stack">
            <h2>Progress</h2>
            <div className="facts">
              <div className="fact"><b>{s.progress.units_completed}/{s.progress.units_total}</b><span>Units completed</span></div>
              <div className="fact"><b>{s.progress.captures}/{s.progress.target}</b><span>Observations</span></div>
              <div className="fact"><b>{s.progress.coverage_percentage.toFixed(0)}%</b><span>Coverage</span></div>
            </div>
            <div className="table-scroll">
              <table className="cmp">
                <thead><tr><th>Unit</th><th>Executive</th><th>Status</th><th>Captures</th></tr></thead>
                <tbody>
                  {units.map((u, i) => (
                    <tr key={u.unit_id}>
                      <td><span className="swatch" style={{ background: COLORS[i % COLORS.length] }} /> <b>{u.unit_code}</b></td>
                      <td>{u.assigned_to_name.split(' (')[0]}</td>
                      <td><span className={`chip${u.status === 'COMPLETED' ? ' yellow' : ''}`}>{u.status.replace('_', ' ').toLowerCase()}</span></td>
                      <td className="num">{u.completed_capture_count}/{u.target_capture_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {s.can_complete && !review && (
          <section className="card stack">
            <h2>All units are complete</h2>
            <p className="hint">Review what the executives recorded. Nothing is saved until you send it to the BD Manager.</p>
            <button className="btn yellow block" disabled={busy !== null} onClick={doReview}>{busy === 'review' ? <span className="loader" /> : null} Review the insights</button>
          </section>
        )}
        {review && (
          <>
            <InsightsView ins={review} title="Insights preview" />
            <div className="two-col">
              <button className="btn ghost" onClick={() => setReview(null)}>Back</button>
              <button className="btn yellow" disabled={busy !== null} onClick={complete}>{busy === 'complete' ? <span className="loader" /> : null} Complete and send to BD Manager</button>
            </div>
          </>
        )}

        {s.status === 'COMPLETED' && s.insights && !s.reused && (
          <>
            <div className="msg info">Completed {fmtTime(s.completed_at)} and sent to the BD Manager.</div>
            <InsightsView ins={s.insights} photos={s.evidence_photos} />
            <button className="btn ghost block" disabled={busy !== null} onClick={regen}>{busy === 'regen' ? <span className="loader" /> : null} Generate a new insights version</button>
          </>
        )}
      </div>
    </div>
  )
}
