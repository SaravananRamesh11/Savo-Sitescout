import { useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { OppDetail, OppRun } from '../../types'
import { fmtTime } from '../common/format'

const km = (m: number) => `${(m / 1000).toFixed(1)} km`
const FEATURE_LABEL: Record<string, string> = {
  competitors_within_1km: 'Supermarkets / convenience stores within 1 km',
  transit_stops_within_500m: 'Transit stops within 500 m',
  population_per_km2: 'Population per km² (estimate)',
  households_per_km2: 'Households per km² (estimate)',
  population_growth_pct: 'Population growth % a year (estimate)',
  road_km_per_km2: 'Road length km per km²',
  nearest_major_road_m: 'Nearest major road (m)',
  nearest_named_road: 'Nearest named road',
}
const KIND: Record<string, string> = {
  property: 'Property captured', assignment: 'Open scouting assignment', catchment_study: 'Completed catchment study',
  catchment_study_active: 'Catchment study in progress',
}
const FLAG_TEXT: Record<string, string> = {
  population_mocked: 'Population is an estimate, not official or current Census data',
  osm_stale_cache: 'Some map data is from an older cache', osm_tile_missing: 'Some areas have no map data',
  overpass_unavailable: 'The map data service was unreachable', osrm_unavailable: 'Road distances were unavailable',
  savomart_stale: 'Store list is from a saved copy', savomart_mocked: 'Store list is a sample snapshot',
}

/** The 3 x 3 block of 500 m cells centred on a cell (the Analyse Area flow accepts contiguous grid cells). */
function block(cellId: string): string[] {
  const [c, r] = cellId.split('_').map(Number)
  const out: string[] = []
  for (let dc = -1; dc <= 1; dc++) for (let dr = -1; dr <= 1; dr++) out.push(`${c + dc}_${r + dr}`)
  return out
}

export default function OpportunityDetail({ run, detail, loading, onClose, onOpenReport }: {
  run: OppRun; detail: OppDetail | null; loading: boolean; onClose: () => void; onOpenReport: (id: number) => void
}) {
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const analyse = async () => {
    if (!detail) return
    setBusy(true)
    setError(null)
    try {
      const area = await api.resolve('grid_cells', block(detail.cell_id))
      const rep = await api.generate(area.area_id)
      onOpenReport(rep.report_id)
    } catch (e) {
      setError((e as ApiError).message || 'Could not start the analysis.')
      setBusy(false)
    }
  }

  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-label="Opportunity details" onClick={onClose}>
      <div className="modal-sheet tall" onClick={(e) => e.stopPropagation()}>
        {loading || !detail ? <div className="empty"><span className="loader" /></div> : !detail.ranked ? (
          <>
            <h2 className="sheet-title">Not ranked</h2>
            <p>{detail.reason_text ?? run.reasons[detail.reason ?? ''] ?? 'This cell has no usable data.'}</p>
            <p className="hint">Cell {detail.cell_id}. Cells like this are shown in grey and never invented a score.</p>
            <button className="btn block ghost" onClick={onClose}>Close</button>
          </>
        ) : (
          <>
            <div className="opp-head">
              <div>
                <h2 className="sheet-title">{detail.rank ? `#${detail.rank} · ` : ''}{detail.locality}</h2>
                <p className="hint">Grid cell {detail.cell_id} · 500 m × 500 m</p>
              </div>
              <div className="opp-score" aria-label={`Opportunity score ${detail.opportunity_score}`}>
                <b>{detail.opportunity_score?.toFixed(1)}</b><small>opportunity</small>
              </div>
            </div>
            <div className="chips">
              <span className="chip yellow">{detail.rating}</span>
              <span className="chip">Area analysis score {detail.m1_score?.toFixed(1)}</span>
              <span className="chip">{detail.scouting && detail.scouting.coverage > 0 ? `${(detail.scouting.coverage * 100).toFixed(0)}% scouted` : 'Unscouted'}</span>
            </div>

            <h3 className="opp-h">Why it scores well</h3>
            <ul className="opp-list">{(detail.positives ?? []).map((p) => <li key={p}>{p}</li>)}</ul>

            <h3 className="opp-h">Scouting so far</h3>
            <p>{detail.scouting?.summary}</p>
            {!!detail.scouting?.signals.length && (
              <ul className="opp-list">
                {detail.scouting.signals.map((s, i) => (
                  <li key={i}>{KIND[s.kind] ?? s.kind}{s.share < 1 ? ' in a neighbouring cell' : ''} · {s.age_days} days ago</li>
                ))}
              </ul>
            )}

            <h3 className="opp-h">Score breakdown</h3>
            <p className="hint">Existing area-analysis factors plus “unscouted opportunity” (weight {((detail.w_unscouted ?? 0) * 100).toFixed(0)}%, an adjustable assumption).</p>
            {(detail.breakdown ?? []).map((b) => (
              <div key={b.key} className={`opp-factor${b.key === 'unscouted_opportunity' ? ' hl' : ''}`}>
                <div><span>{b.label}</span><span>{b.points.toFixed(1)} / {b.weight}</span></div>
                <div className="meter dark"><i style={{ width: `${Math.min(100, b.weight ? (b.points / b.weight) * 100 : 0)}%` }} /></div>
                <small className="hint">{b.explanation}</small>
              </div>
            ))}

            <h3 className="opp-h">Around it</h3>
            <dl className="opp-dl">
              {Object.entries(detail.features ?? {}).filter(([k]) => k in FEATURE_LABEL).map(([k, v]) => (
                <div key={k}><dt>{FEATURE_LABEL[k]}</dt><dd>{v === null || v === undefined ? 'Insufficient data' : typeof v === 'number' ? Math.round(v * 100) / 100 : String(v)}</dd></div>
              ))}
              {(() => {
                const c = (detail.features?.mapped_in_this_cell ?? {}) as Record<string, number>
                const e = Object.entries(c)
                return <div><dt>Mapped inside this cell</dt><dd>{e.length ? e.map(([k, n]) => `${n} ${k}`).join(', ') : 'nothing mapped'}</dd></div>
              })()}
            </dl>

            <h3 className="opp-h">Nearest Savomart stores</h3>
            {(detail.nearest_stores ?? []).length === 0 && <p className="hint">Insufficient data.</p>}
            <ul className="opp-list">
              {(detail.nearest_stores ?? []).map((s, i) => (
                <li key={s.name}>{s.name}: {km(s.distance_m)} in a straight line
                  {i === 0 && s.road_distance_m !== undefined ? `, ${km(s.road_distance_m)} by road (about ${Math.round((s.road_duration_s ?? 0) / 60)} min)` : ''}</li>
              ))}
            </ul>

            <h3 className="opp-h">Risks and data quality</h3>
            <ul className="opp-list">{(detail.risks ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
            <div className="chips">
              {run.data_quality_flags.map((f) => <span key={f} className="chip warn">{FLAG_TEXT[f] ?? f}</span>)}
            </div>
            <details className="opp-src">
              <summary>Data sources</summary>
              <ul className="opp-list">
                {run.data_sources.map((s) => (
                  <li key={s.source}>{s.source}{s.mocked ? ' (estimate / sample)' : ''}{s.fetched_at ? ` · fetched ${fmtTime(s.fetched_at)}` : ''}{s.note ? ` · ${s.note}` : ''}</li>
                ))}
              </ul>
              {detail.map_data?.fetched_at && <p className="hint">Map data for this cell: {detail.map_data.status}, fetched {fmtTime(detail.map_data.fetched_at)}.</p>}
            </details>

            {error && <div className="msg err">{error}</div>}
            {!confirm ? (
              <div className="opp-actions">
                <button className="btn block yellow" onClick={() => setConfirm(true)}>Analyse this pocket</button>
                <a className="btn block ghost" href={`https://www.openstreetmap.org/?mlat=${detail.lat}&mlon=${detail.lon}#map=17/${detail.lat}/${detail.lon}`} target="_blank" rel="noreferrer">Open in OpenStreetMap</a>
                <button className="btn block ghost" onClick={onClose}>Close</button>
              </div>
            ) : (
              <div className="opp-confirm">
                <p>This runs the normal Analyse Area report on the 3 × 3 cells (about 2.25 km²) around this cell. It does not create a property, an assignment or a survey.</p>
                <div className="opp-actions">
                  <button className="btn block" onClick={analyse} disabled={busy}>{busy ? 'Starting…' : 'Run the analysis'}</button>
                  <button className="btn block ghost" onClick={() => setConfirm(false)} disabled={busy}>Cancel</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
