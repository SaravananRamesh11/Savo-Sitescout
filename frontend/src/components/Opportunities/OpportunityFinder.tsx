import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../../api/client'
import { OPP } from '../../theme/colors'
import type { OppDetail, OppRun, OppStatus } from '../../types'
import { fmtTime } from '../common/format'
import OpportunityDetail from './OpportunityDetail'
import OpportunityMap, { type OppMode } from './OpportunityMap'

/** Opportunity Finder: "where should the BD team scout next?" A separate page with its own map. It scans the whole city and
 * ranks unscouted, high-potential 500 m cells; the manager then decides whether to open one in the normal Analyse Area flow. */
export default function OpportunityFinder({ onOpenReport }: { onOpenReport: (id: number) => void }) {
  const [run, setRun] = useState<OppRun | null>(null)
  const [active, setActive] = useState<OppStatus | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<OppMode>('opportunity')
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<OppDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const alive = useRef(true)

  const poll = useCallback(function again(id: number) {
    window.clearTimeout(timer.current)
    api.oppStatus(id)
      .then(async (s) => {
        if (!alive.current) return
        if (s.status === 'completed') {
          const r = await api.oppRun(id)
          if (!alive.current) return
          setRun(r)
          setActive(null)
        } else if (s.status === 'failed') {
          setActive(null)
          setError(s.error || 'The search failed. Please try again.')
        } else {
          setActive(s)
          timer.current = window.setTimeout(() => again(id), 3000)
        }
      })
      .catch((e: ApiError) => {
        if (!alive.current) return
        setError(e.message)
        timer.current = window.setTimeout(() => again(id), 6000)
      })
  }, [])

  useEffect(() => {
    alive.current = true
    api.oppLatest()
      .then((r) => {
        if (!alive.current) return
        setRun(r.run)
        if (r.active) { setActive(r.active); poll(r.active.run_id) }
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => alive.current && setLoaded(true))
    return () => { alive.current = false; window.clearTimeout(timer.current) }
  }, [poll])

  const start = () => {
    setError(null)
    api.oppStart()
      .then((r) => {
        setActive({ run_id: r.run_id, status: 'queued', error: null, created_at: '', completed_at: null,
          progress: { phase: 'queued', done: 0, total: 0, message: 'Starting…' } })
        poll(r.run_id)
      })
      .catch((e: ApiError) => setError(e.message))
  }

  const select = (id: string) => {
    if (!run) return
    setSelected(id)
    const inTop = run.top.find((t) => t.cell_id === id)
    if (inTop) { setDetail({ ...inTop, ranked: true }); setDetailLoading(false); return }
    setDetail(null)
    setDetailLoading(true)
    api.oppCell(run.run_id, id)
      .then((d) => { setDetail(d); setDetailLoading(false) })
      .catch((e: ApiError) => { setError(e.message); setDetailLoading(false); setSelected(null) })
  }
  const close = () => { setSelected(null); setDetail(null) }

  const running = active !== null
  const pct = active && active.progress.total ? Math.min(100, Math.round((active.progress.done / active.progress.total) * 100)) : 4
  const stores = useMemo(() => run?.summary.stores ?? [], [run])
  const w = run ? Math.round(run.config.w_unscouted * 100) : 0

  return (
    <div className="scroll-view">
      <div className="page stack">
        <div>
          <h1 style={{ fontSize: 22 }}>Opportunity Finder</h1>
          <p className="hint">Where should we scout next? Scans all of Chennai for high-potential pockets that have had little or no scouting. Nothing is created until you choose to analyse one.</p>
        </div>

        <section className="card opp-run">
          <button className="btn block yellow" onClick={start} disabled={running || !loaded}>
            {running ? 'Searching Chennai…' : run ? 'Find opportunities again' : 'Find Opportunities'}
          </button>
          {running && active && (
            <div role="status" aria-live="polite">
              <div className="meter dark" aria-label="Progress"><i style={{ width: `${pct}%` }} /></div>
              <p className="hint">{active.progress.message}. The first search reads the city&apos;s map data in pieces and can take several minutes; later searches reuse it and take seconds.</p>
            </div>
          )}
          {run && !running && (
            <p className="hint">Last search {fmtTime(run.completed_at ?? run.created_at)} · {run.summary.cells_ranked.toLocaleString()} of {run.summary.cells_total.toLocaleString()} cells ranked
              {run.summary.tiles_missing ? ` · map data missing for ${run.summary.tiles_missing} of ${run.summary.tiles_total} areas` : ''}</p>
          )}
          {error && <div className="msg err">{error}</div>}
        </section>

        {!run && loaded && !running && (
          <div className="empty card"><h2>No search yet</h2><p>Tap “Find Opportunities” to score every 500 m square in Chennai and list the best places nobody has scouted.</p></div>
        )}

        {run && (
          <div className="page-grid two">
            <div className="sticky-col">
              <section className="card opp-map-card">
                <div className="filter-row" role="group" aria-label="Map colours">
                  <button className={`pill${mode === 'opportunity' ? ' on' : ''}`} aria-pressed={mode === 'opportunity'} onClick={() => setMode('opportunity')}>Opportunity score</button>
                  <button className={`pill${mode === 'coverage' ? ' on' : ''}`} aria-pressed={mode === 'coverage'} onClick={() => setMode('coverage')}>Scouting coverage</button>
                </div>
                <div className="opp-map">
                  <OpportunityMap runId={run.run_id} cells={run.cells} mode={mode} selectedId={selected}
                    top={run.top} stores={stores} onSelect={select} />
                </div>
                <div className="opp-legend" aria-label="Legend">
                  <span>{mode === 'opportunity' ? 'Lower' : 'Not scouted'}</span>
                  <i style={{ background: `linear-gradient(90deg, ${mode === 'opportunity' ? `${OPP.ramp(0)}, ${OPP.ramp(1)}` : `${OPP.rampCoverage(0)}, ${OPP.rampCoverage(1)}`})` }} />
                  <span>{mode === 'opportunity' ? 'Higher (colours show rank among all ranked squares)' : 'Fully scouted'}</span>
                </div>
                <div className="opp-legend2">
                  <span><b className="opp-pin sm">1</b> top opportunity</span>
                  <span><b className="dot store" /> Savomart store</span>
                  <span><b className="dot grey" /> not ranked</span>
                </div>
                <p className="hint">Tap any square for its score and reasons.</p>
              </section>
            </div>

            <div className="stack">
              <section className="card">
                <h2>Top {run.top.length} opportunities</h2>
                {run.top.length === 0 && <p className="hint">Nothing could be ranked: there was not enough map data. Try again later.</p>}
                {run.top.map((t) => (
                  <article key={t.cell_id} className="hotspot opp-row" onClick={() => select(t.cell_id)} role="button" tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') select(t.cell_id) }}>
                    <div className="rank" aria-label={`Rank ${t.rank}`}>{t.rank}</div>
                    <div>
                      <h3>{t.locality} <span className="score">{t.opportunity_score?.toFixed(1)}</span></h3>
                      <p className="meta">Cell {t.cell_id} · {t.scouting?.summary}</p>
                      <ul>{(t.positives ?? []).slice(0, 2).map((p) => <li key={p}>{p}</li>)}</ul>
                    </div>
                  </article>
                ))}
              </section>

              <details className="card opp-assume">
                <summary>How this is scored, and what to keep in mind</summary>
                <p><b>Opportunity score</b> = {100 - w}% the existing area-analysis score (residential demand, accessibility, commercial activity, amenities, competition, distance from Savomart stores) + {w}% “unscouted opportunity”.</p>
                <p><b>Scouting coverage</b> comes from your properties, open assignments and catchment studies, fading over a year: a lone property lowers a square&apos;s priority a little, a finished catchment study lowers it a lot.</p>
                <p>The {w}% weight and the coverage rules are adjustable starting assumptions, not business facts.</p>
                <ul className="opp-list">
                  {run.data_quality_flags.includes('population_mocked') && <li>Population figures are estimates (Census-2011 order of magnitude), not official or current Census data.</li>}
                  {run.data_sources.map((s) => <li key={s.source}>{s.source}{s.fetched_at ? ` · ${fmtTime(s.fetched_at)}` : ''}</li>)}
                </ul>
              </details>
            </div>
          </div>
        )}
      </div>

      {run && selected && (
        <OpportunityDetail run={run} detail={detail} loading={detailLoading} onClose={close} onOpenReport={onOpenReport} />
      )}
    </div>
  )
}
