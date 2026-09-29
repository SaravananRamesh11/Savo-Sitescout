import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { Compare, ReportSummary } from '../../types'
import { fmtNum, fmtTime } from '../common/format'

function CompareView({ ids, onClose }: { ids: number[]; onClose: () => void }) {
  const [data, setData] = useState<Compare | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.compare(ids).then(setData).catch((e: ApiError) => setError(e.message))
  }, [ids])
  if (error) return <div className="msg err">{error} <button className="btn ghost sm" onClick={onClose}>Back</button></div>
  if (!data) return <div className="empty"><span className="loader" /></div>
  const best = data.best_report_id
  const cols = data.reports
  const pts = (row: Compare['factors'][number], id: number) => (row[String(id)] as { points: number }).points
  return (
    <div className="stack">
      <button className="btn ghost sm" onClick={onClose}>← Back to reports</button>
      <section className="card">
        <h2>Comparison</h2>
        <div className="table-scroll">
          <table className="cmp">
            <thead>
              <tr>
                <th>Signal (max)</th>
                {cols.map((r) => (
                  <th key={r.report_id} className={r.report_id === best ? 'best' : ''}>
                    {r.area_name}
                    <div className="hint">#{r.report_id} · {fmtTime(r.created_at)}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><b>Overall</b></td>
                {cols.map((r) => (
                  <td key={r.report_id} className={`num${r.report_id === best ? ' best' : ''}`}>
                    <b>{r.overall_score?.toFixed(1)}</b> · {r.rating}
                  </td>
                ))}
              </tr>
              {data.factors.map((f) => {
                const top = Math.max(...cols.map((r) => pts(f, r.report_id)))
                return (
                  <tr key={f.key}>
                    <td>{f.label} ({f.weight})</td>
                    {cols.map((r) => (
                      <td key={r.report_id} className={`num${pts(f, r.report_id) === top && cols.length > 1 ? ' best' : ''}`}>
                        {pts(f, r.report_id).toFixed(1)}
                      </td>
                    ))}
                  </tr>
                )
              })}
              <tr>
                <td>Estimated people</td>
                {cols.map((r) => <td className="num" key={r.report_id}>{fmtNum(r.profile.estimated_population)}</td>)}
              </tr>
              <tr>
                <td>Nearest Savomart (m)</td>
                {cols.map((r) => <td className="num" key={r.report_id}>{fmtNum(r.profile.nearest_savomart_m)}</td>)}
              </tr>
              <tr>
                <td>Top hotspot</td>
                {cols.map((r) => (
                  <td key={r.report_id}>{r.hotspots[0] ? `${r.hotspots[0].locality} (${r.hotspots[0].score})` : '-'}</td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
        <p className="hint" style={{ marginTop: 10 }}>Highlighted cells score highest in each row. Reports were generated at different times, so check the timestamps.</p>
      </section>
    </div>
  )
}

export default function ReportList({ onOpen }: { onOpen: (id: number) => void }) {
  const [items, setItems] = useState<ReportSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pick, setPick] = useState<number[]>([])
  const [comparing, setComparing] = useState(false)

  useEffect(() => {
    const load = () => api.reports().then((r) => { setItems(r); setError(null) }).catch((e: ApiError) => setError(e.message))
    load()
    const t = window.setInterval(() => {
      // refresh while anything is still running
      if (items?.some((i) => i.status === 'queued' || i.status === 'running')) load()
    }, 4000)
    return () => window.clearInterval(t)
  }, [items])

  const toggle = (id: number) =>
    setPick((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < 4 ? [...p, id] : p))

  if (comparing) return <div className="scroll-view"><div className="page"><CompareView ids={pick} onClose={() => setComparing(false)} /></div></div>

  return (
    <div className="scroll-view">
      <div className="page stack">
        <div>
          <h1 style={{ fontSize: 22 }}>Saved reports</h1>
          <p className="hint">Every analysis is saved with the time it ran and the data it used. Tick 2 to 4 completed reports to compare them.</p>
        </div>
        {error && <div className="msg err">{error}</div>}
        {!items && !error && <div className="empty"><span className="loader" /></div>}
        {items && items.length === 0 && (
          <div className="empty"><h2>No reports yet</h2><p>Run a virtual analysis from the Analyse tab.</p></div>
        )}
        {items && items.length > 0 && (
          <section className="card">
            {items.map((r) => (
              <div className="report-item" key={r.report_id}>
                <input
                  type="checkbox"
                  aria-label={`Select report ${r.report_id} for comparison`}
                  checked={pick.includes(r.report_id)}
                  disabled={r.status !== 'completed'}
                  onChange={() => toggle(r.report_id)}
                />
                <button style={{ background: 'none', border: 0, textAlign: 'left', padding: 0 }} onClick={() => onOpen(r.report_id)}>
                  <div className="t">{r.area_name}</div>
                  <div className="s">
                    #{r.report_id} · {fmtTime(r.created_at)} · {r.area_km2} km²
                  </div>
                  <div className="chips">
                    {r.status !== 'completed' && (
                      <span className={`chip${r.status === 'failed' ? ' warn' : ' yellow'}`}>{r.status}</span>
                    )}
                    {r.rating && <span className="chip">{r.rating}</span>}
                    {r.data_quality_flags.length > 0 && <span className="chip warn">{r.data_quality_flags.length} data notes</span>}
                  </div>
                </button>
                <div className="mini-score">{r.overall_score != null ? r.overall_score.toFixed(1) : '·'}</div>
              </div>
            ))}
          </section>
        )}
        {pick.length >= 2 && (
          <button className="btn yellow block" onClick={() => setComparing(true)}>Compare {pick.length} reports</button>
        )}
      </div>
    </div>
  )
}
