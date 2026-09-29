import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Circle, MapContainer, Marker, TileLayer, useMap } from 'react-leaflet'
import { api, ApiError, getPersona } from '../../api/client'
import type { EvalFactor, PropertyDetail, Stage } from '../../types'
import { FLAG_TEXT, fmtTime } from '../common/format'
import { Fact, Field, inr, num, pct, REC_LABEL, StageChip, STAGE_LABEL } from '../common/ui'

const pinIcon = L.divIcon({ className: '', html: '<div class="drop-pin"><span></span></div>', iconSize: [34, 42], iconAnchor: [17, 40] })
const FLAG_EXTRA: Record<string, string> = {
  osm_unavailable: 'OpenStreetMap was unreachable, so nearby demand and competition could not be scored.',
  no_m1_report: 'This area has no completed M1 area analysis yet.',
}
const yn = (v: boolean | null | undefined) => (v == null ? '-' : v ? 'Yes' : 'No')
const cap = (s?: string | null) => (s ? s[0].toUpperCase() + s.slice(1) : '-')

function AutoResize() {
  const map = useMap()
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  return null
}

function Gallery({ p }: { p: PropertyDetail }) {
  const [open, setOpen] = useState<string | null>(null)
  if (!p.photos.length) return <div className="gallery empty-g">No photos captured</div>
  return (
    <>
      <div className="gallery" aria-label="Property photos">
        {p.photos.map((ph) => (
          <button key={ph.photo_id} className="g-item" onClick={() => setOpen(ph.url)}>
            <img src={ph.url} alt={ph.photo_type.replace('_', ' ')} loading="lazy" />
            <span>{ph.photo_type.replace('_', ' ')}</span>
          </button>
        ))}
      </div>
      {open && <div className="lightbox" onClick={() => setOpen(null)} role="presentation"><img src={open} alt="Property" /></div>}
    </>
  )
}

function Breakdown({ factors }: { factors: EvalFactor[] }) {
  return (
    <section className="card">
      <h2>How the score was built</h2>
      <p className="hint" style={{ marginBottom: 10 }}>Weights are configurable assumptions. Factors without enough data are left out and the rest are rescaled.</p>
      {factors.map((f) => (
        <div className="bd-row static" key={f.key}>
          <div className="bd-top">
            <span>{f.label}{f.estimated_input ? ' (estimated input)' : ''}</span>
            <b>{f.scored ? `${f.points?.toFixed(1)} / ${f.effective_weight.toFixed(1)}` : 'Insufficient data'}</b>
          </div>
          <div className={`bd-track${f.scored ? '' : ' hatched'}`}>{f.scored && <div className="bd-fill" style={{ width: `${(f.norm ?? 0) * 100}%` }} />}</div>
          <div className="bd-explain">{f.explanation}</div>
        </div>
      ))}
    </section>
  )
}

export default function PropertyReview({ id, onBack, onEdit }: { id: number; onBack: () => void; onEdit: (id: number) => void }) {
  const [p, setP] = useState<PropertyDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [act, setAct] = useState<Stage | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [rev, setRev] = useState<{ v: string; src: string }>({ v: '', src: 'Manager assumption' })
  const timer = useRef<number | undefined>(undefined)
  const isManager = getPersona().startsWith('bd_manager')

  const load = useCallback(async () => {
    try {
      const d = await api.property(id)
      setP(d)
      setError(null)
      if (d.latest_evaluation_status?.status === 'running') timer.current = window.setTimeout(load, 3000)
    } catch (e) { setError((e as ApiError).message) }
  }, [id])
  useEffect(() => { load(); return () => window.clearTimeout(timer.current) }, [load])

  if (error && !p) return <div className="scroll-view"><div className="page stack"><button className="btn ghost sm" onClick={onBack}>← Back</button><div className="msg err">{error}</div></div></div>
  if (!p) return <div className="scroll-view"><div className="empty"><span className="loader" /></div></div>

  const ev = p.evaluation
  const m = ev?.metrics
  const c = m?.poi?.counts
  const org = m?.poi?.organised
  const running = p.latest_evaluation_status?.status === 'running'
  const failed = p.latest_evaluation_status?.status === 'failed'
  const flags = (ev?.data_quality_flags ?? []).filter((f) => FLAG_TEXT[f] || FLAG_EXTRA[f])
  const NEEDS_REASON: Stage[] = ['REJECTED', 'APPROVED', 'ASSIGNED']  // catchment request needs no typed text
  const inFinal = p.pipeline_stage === 'FINAL_REVIEW'
  const afterCatchment = ['CATCHMENT_REQUESTED', 'CATCHMENT_IN_PROGRESS', 'CATCHMENT_COMPLETED', 'FINAL_REVIEW', 'APPROVED', 'REJECTED'].includes(p.pipeline_stage)
    && p.history.some((h) => h.to_stage === 'CATCHMENT_REQUESTED')

  const doTransition = async (to: Stage) => {
    setBusy(true); setNote(null)
    try {
      setP(await api.transition(id, to, reason.trim() || undefined))
      setAct(null); setReason('')
    } catch (e) { setNote((e as ApiError).message) } finally { setBusy(false) }
  }
  const reEval = async () => {
    setBusy(true); setNote(null)
    try { await api.reEvaluate(id); await load(); setNote('Re-evaluating. A new version will appear here.') } catch (e) { setNote((e as ApiError).message) } finally { setBusy(false) }
  }
  const saveRevenue = async () => {
    const v = Number(rev.v)
    if (!v || v <= 0) { setNote('Enter an expected monthly revenue greater than zero.'); return }
    setBusy(true); setNote(null)
    try {
      await api.patchProperty(id, { expected_monthly_revenue: v, revenue_source: rev.src })
      await api.reEvaluate(id); await load(); setNote('Revenue saved. Re-evaluating with the rent-to-revenue ratio.')
    } catch (e) { setNote((e as ApiError).message) } finally { setBusy(false) }
  }

  const label = (s: Stage) =>
    ({ UNDER_REVIEW: 'Start review', REJECTED: inFinal ? 'Reject property (final)' : 'Reject', APPROVED: 'Approve property (final)',
       CATCHMENT_REQUESTED: 'Request catchment study', ASSIGNED: 'Send back for changes', FINAL_REVIEW: 'Start final review' } as Record<string, string>)[s] ?? STAGE_LABEL[s]

  return (
    <div className="scroll-view">
      <div className="page stack">
        <button className="btn ghost sm" onClick={onBack}>← Back</button>
        <div>
          <h1 style={{ fontSize: 22 }}>{p.address ?? `Property #${p.property_id}`}</h1>
          <p className="hint">#{p.property_id} · {p.locality ?? p.area_name} · captured by {p.created_by_name} · {fmtTime(p.created_at)}</p>
          <div className="chips" style={{ marginTop: 8 }}><StageChip stage={p.pipeline_stage} sentBack={p.submitted_at != null} />{p.possible_duplicate && <span className="chip warn">possible duplicate</span>}</div>
        </div>
        {p.duplicate_flags.length > 0 && (
          <div className="msg warn">Possible duplicate of {p.duplicate_flags.map((d) => `#${d.property_id} (${d.distance_m} m)`).join(', ')}. Nothing was merged. Compare before deciding.{' '}
            {p.duplicate_flags.map((d) => <a key={d.property_id} href={`#/property/${d.property_id}`} style={{ marginRight: 8 }}>Open #{d.property_id}</a>)}</div>
        )}

        <div className="page-grid two">
          <div className="stack">
            <Gallery p={p} />

            <section className="score-hero" aria-label="Property evaluation">
              <div className="stack" style={{ gap: 8 }}>
                <div className="name">Property evaluation</div>
                {running ? <div className="score-num"><span className="loader" style={{ borderTopColor: '#FFF200' }} /></div> : (
                  <div className="score-num">{ev?.overall_score != null ? ev.overall_score.toFixed(1) : '–'}<small> / 100</small></div>
                )}
                {ev?.recommendation && <div><span className="score-rating">{REC_LABEL[ev.recommendation] ?? ev.recommendation}</span></div>}
              </div>
              <div className="stack" style={{ gap: 10 }}>
                <div className="meter"><i style={{ width: `${Math.min(ev?.overall_score ?? 0, 100)}%` }} /></div>
                <div className="score-meta">
                  {running && 'Evaluating public data around the property…'}
                  {failed && `Evaluation failed: ${p.latest_evaluation_status?.error ?? 'unknown error'}`}
                  {ev && <>Evaluation v{ev.version} · {fmtTime(ev.created_at)}<br />
                    {ev.confidence != null && <>Confidence: {(ev.confidence * 100).toFixed(0)}% of the scoring weight could be assessed<br /></>}
                    Explained by {ev.explanation_source === 'llm' ? 'AI (number-checked)' : 'fixed template'}
                    {p.previous_evaluation && <><br />Previous: v{p.previous_evaluation.version} scored {p.previous_evaluation.overall_score?.toFixed(1) ?? '-'}</>}</>}
                  {!ev && !running && !failed && 'Not evaluated yet. It runs automatically when the property is submitted.'}
                </div>
              </div>
            </section>

            {flags.length > 0 && <div className="stack">{flags.map((f) => <div key={f} className="msg warn">{FLAG_TEXT[f] ?? FLAG_EXTRA[f]}</div>)}</div>}

            {ev?.explanation && (
              <section className="card">
                <h2>Recommendation</h2>
                <p className="summary">{ev.explanation.summary}</p>
              </section>
            )}

            {(ev?.risks?.length ?? 0) > 0 && (
              <section className="card">
                <h2>Key risks</h2>
                <ul className="list-plain">
                  {ev!.risks!.map((r) => (
                    <li key={r.code}><span className={`chip sev-${r.severity}`}>{r.severity}</span> {r.text}</li>
                  ))}
                </ul>
              </section>
            )}


            {afterCatchment && (
              <section className="card stack" aria-label="Catchment and final review">
                <h2>{p.final_decision ? 'Final decision' : inFinal ? 'Final review' : 'Catchment study'}</h2>
                {p.final_decision && (
                  <div className={`msg ${p.final_decision.decision === 'APPROVED' ? 'info' : 'err'}`} role="status">
                    <b>{p.final_decision.decision === 'APPROVED' ? 'Approved' : 'Rejected'}</b> by {p.final_decision.decided_by_name} on {fmtTime(p.final_decision.decided_at)}
                    {p.final_decision.reason && <div className="tl-reason">“{p.final_decision.reason}”</div>}
                    {p.final_decision.based_on_evaluation && (
                      <div className="hint" style={{ marginTop: 4 }}>
                        Based on evaluation v{p.final_decision.based_on_evaluation.version}, score {p.final_decision.based_on_evaluation.overall_score?.toFixed(1) ?? '-'}. Kept for audit.
                      </div>
                    )}
                  </div>
                )}
                {(inFinal || p.final_decision || p.pipeline_stage === 'CATCHMENT_COMPLETED') && (
                  <div className="eval-compare">
                    <div>
                      <span>Original M2 evaluation</span>
                      <b>{p.original_evaluation?.overall_score != null ? p.original_evaluation.overall_score.toFixed(1) : '-'}</b>
                      <em>{p.original_evaluation ? `v${p.original_evaluation.version} · ${fmtTime(p.original_evaluation.created_at)}` : 'not available'}</em>
                    </div>
                    <div className="arrow" aria-hidden>→</div>
                    <div className="updated">
                      <span>Updated for final review</span>
                      <b>{p.updated_evaluation?.overall_score != null ? p.updated_evaluation.overall_score.toFixed(1) : running ? '…' : 'pending'}</b>
                      <em>{p.updated_evaluation ? `v${p.updated_evaluation.version} · ${fmtTime(p.updated_evaluation.created_at)}` : 'created when final review starts'}</em>
                    </div>
                  </div>
                )}
                {p.score_change != null && (
                  <p className="hint">Score change since the original evaluation: <b>{p.score_change > 0 ? '+' : ''}{p.score_change}</b>. Both versions stay available.</p>
                )}
                {p.catchment == null && (
                  <div className="msg warn">
                    <b>No ground-survey (catchment) insights are recorded for this property yet.</b> The catchment study module is
                    not connected, so this evaluation uses the same public and field data as before. Nothing has been estimated or
                    made up. Decide with that in mind.
                  </div>
                )}
                {inFinal && (
                  <p className="hint">Review the full evaluation below, then approve or reject. Your reason is required and is kept with the evaluation version you saw.</p>
                )}
              </section>
            )}

            {(p.allowed_next_stages.length > 0 || (isManager && p.pipeline_stage !== 'ASSIGNED') || (p.can_edit && !isManager)) && (
              <section className="card stack" aria-label="Actions">
                <h2>Decision</h2>
                {note && <div className="msg info" role="status">{note}</div>}
                {!isManager && p.can_edit && <button className="btn yellow block" onClick={() => onEdit(p.property_id)}>Continue editing this draft</button>}
                {isManager && p.allowed_next_stages.map((s) => (
                  <button key={s} className={`btn block${s === 'REJECTED' ? ' ghost' : s === 'CATCHMENT_REQUESTED' ? ' yellow' : ''}`} disabled={busy}
                    onClick={() => (NEEDS_REASON.includes(s) ? setAct(act === s ? null : s) : doTransition(s))}>{label(s)}</button>
                ))}
                {act && (
                  <div className="stack">
                    <Field label={act === 'REJECTED' ? 'Why is it rejected?' : act === 'APPROVED' ? 'Why is it approved?' : 'What should the executive fix?'}
                      hint={inFinal ? 'Saved permanently in the audit history together with the evaluation version you are looking at.' : undefined} required>
                      <textarea className="input" style={{ minHeight: 84, paddingTop: 10 }} value={reason} onChange={(e) => setReason(e.target.value)} />
                    </Field>
                    <button className="btn block" disabled={busy || !reason.trim()} onClick={() => doTransition(act)}>Confirm: {label(act)}</button>
                  </div>
                )}
                {isManager && p.pipeline_stage !== 'ASSIGNED' && (
                  <>
                    <button className="btn ghost block" disabled={busy || running} onClick={reEval}>Re-evaluate now</button>
                    {p.expected_monthly_revenue == null && (
                      <details className="revenue-box">
                        <summary>Add expected monthly revenue (optional)</summary>
                        <p className="hint" style={{ margin: '6px 0' }}>Only enter a figure you trust. Without it, rent affordability stays “Insufficient data”. Nothing is estimated for you.</p>
                        <div className="two-col">
                          <input className="input" type="number" inputMode="numeric" placeholder="₹ per month" value={rev.v} onChange={(e) => setRev({ ...rev, v: e.target.value })} />
                          <input className="input" value={rev.src} onChange={(e) => setRev({ ...rev, src: e.target.value })} aria-label="Source of the estimate" />
                        </div>
                        <button className="btn sm block" style={{ marginTop: 8 }} disabled={busy} onClick={saveRevenue}>Save and re-evaluate</button>
                      </details>
                    )}
                  </>
                )}
              </section>
            )}

            <section className="card">
              <h2>Commercial</h2>
              <div className="facts">
                <Fact label="Monthly rent" value={inr(p.monthly_rent)} />
                <Fact label="Rent per sq ft" value={m?.rent_per_sqft != null ? `₹${m.rent_per_sqft}` : p.rent_per_sqft != null ? `₹${p.rent_per_sqft}` : '-'} />
                <Fact label="Total area" value={num(p.total_area_sqft, ' sq ft')} />
                <Fact label="Lease" value={p.lease_duration_months ? `${p.lease_duration_months} mo` : '-'} sub={`Deposit ${inr(p.security_deposit)}`} />
                <Fact label="Negotiable" value={yn(p.rent_negotiable)} />
                <Fact label="Rent / revenue" value={m?.rent_to_revenue != null ? pct(m.rent_to_revenue, 1) : 'Insufficient data'}
                  sub={m?.rent_to_revenue != null ? p.revenue_source : 'no revenue estimate provided'} />
              </div>
            </section>

            <section className="card">
              <h2>Access and visibility</h2>
              <div className="facts">
                <Fact label="Main-road frontage" value={yn(p.is_main_road_frontage)} />
                <Fact label="Corner property" value={yn(p.is_corner_property)} />
                <Fact label="Road width" value={num(p.road_width_ft, ' ft')} />
                <Fact label="Frontage" value={num(p.frontage_ft, ' ft')} />
                <Fact label="Visibility" value={p.visibility_score ? `${p.visibility_score} / 5` : '-'} />
                <Fact label="Entry / exit" value={`${cap(p.entry_access)} / ${cap(p.exit_access)}`} />
                <Fact label="Traffic signal" value={p.traffic_signal_nearby == null ? '-' : p.traffic_signal_nearby ? `Yes${p.signal_distance_m ? ` (${p.signal_distance_m} m)` : ''}` : 'No'} sub="not automatically good" />
              </div>
            </section>

            <section className="card">
              <h2>Demand around the property</h2>
              {m?.poi?.available && c ? (
                <div className="facts">
                  <Fact label="Schools 250 m / 500 m" value={`${c.school?.['250'] ?? 0} / ${c.school?.['500'] ?? 0}`} />
                  <Fact label="Colleges 250 m / 500 m" value={`${c.college?.['250'] ?? 0} / ${c.college?.['500'] ?? 0}`} />
                  <Fact label="Hospitals 250 m / 500 m" value={`${c.hospital?.['250'] ?? 0} / ${c.hospital?.['500'] ?? 0}`} />
                  {m.demographics && <Fact label="Households / km²" value={num(Math.round(m.demographics.household_density))} sub={m.demographics.mocked ? 'estimate' : undefined} tone={m.demographics.mocked ? 'warn' : undefined} />}
                  {m.demographics && <Fact label="People / km²" value={num(Math.round(m.demographics.pop_density))} sub={m.demographics.mocked ? 'estimate' : undefined} />}
                </div>
              ) : <p className="hint">No real OpenStreetMap data was available for demand signals.</p>}
              <p className="hint" style={{ marginTop: 8 }}>These are trip-generator signals, not guaranteed customers. Income fit is not assessed: there is no income data or Savomart target range.</p>
            </section>

            <section className="card">
              <h2>Competition</h2>
              <div className="facts">
                <Fact label="Organised, within 500 m" value={org ? org['500'] ?? 0 : '-'} />
                <Fact label="Organised, within 1 km" value={org ? org['1000'] ?? 0 : '-'} />
                <Fact label="Nearest organised" value={m?.poi?.nearest_organised_m != null ? `${m.poi.nearest_organised_m} m` : '-'} />
                <Fact label="Field-observed" value={p.field_competitors.length} />
              </div>
              {p.field_competitors.length > 0 && (
                <ul className="list-plain" style={{ marginTop: 10 }}>
                  {p.field_competitors.map((x) => <li key={x.id}><b>{x.name}</b> · {x.kind.replace('_', ' ')}{x.approx_distance_m != null ? ` · about ${x.approx_distance_m} m` : ''}</li>)}
                </ul>
              )}
              <p className="hint" style={{ marginTop: 8 }}>No competition is not automatically good: it is scored together with demand. Public and field counts overlap, so they are never added.</p>
            </section>

            <section className="card">
              <h2>Space and parking</h2>
              <div className="facts">
                <Fact label="Total / ground" value={`${num(p.total_area_sqft)} / ${num(p.ground_floor_area_sqft)}`} sub="sq ft" />
                <Fact label="Sales / storage" value={`${num(p.sales_area_sqft)} / ${num(p.storage_area_sqft)}`} sub="sq ft, if measured" />
                <Fact label="Sales share" value={m?.sales_ratio != null ? pct(m.sales_ratio) : 'Not measured'} />
                <Fact label="Storage : sales" value={m?.storage_to_sales != null ? m.storage_to_sales.toFixed(2) : 'Not measured'} />
                <Fact label="Two-wheeler" value={yn(p.two_wheeler_parking)} />
                <Fact label="Four-wheeler" value={yn(p.four_wheeler_parking)} sub={p.parking_capacity != null ? `capacity ${p.parking_capacity}` : undefined} />
              </div>
            </section>

            {ev?.score_breakdown && <Breakdown factors={ev.score_breakdown} />}
          </div>

          <div className="stack sticky-col">
            <div className="report-map">
              <MapContainer maxZoom={21} center={[p.lat, p.lon]} zoom={17} style={{ height: '100%' }} scrollWheelZoom={false}>
                <TileLayer maxZoom={21} maxNativeZoom={19} attribution='&copy; OpenStreetMap contributors' url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <Marker position={[p.lat, p.lon]} icon={pinIcon} />
                <AutoResize />
                {p.location_accuracy_m != null && p.location_source === 'gps' && <Circle center={[p.lat, p.lon]} radius={p.location_accuracy_m} pathOptions={{ color: '#782B90', weight: 1, fillOpacity: 0.1 }} />}
              </MapContainer>
            </div>
            <p className="hint">Pin {p.lat.toFixed(5)}, {p.lon.toFixed(5)} · {p.location_source === 'gps' ? `device location${p.location_accuracy_m != null ? `, about ${Math.round(p.location_accuracy_m)} m accuracy` : ''}` : 'placed on the map'}</p>

            <section className="card">
              <h2>Area context (M1)</h2>
              {ev?.m1_context?.area_report_id ? (
                <div className="facts">
                  <Fact label="Area fitness" value={ev.m1_context.area_score != null ? ev.m1_context.area_score.toFixed(1) : '-'} sub={<a href={`#/report/${ev.m1_context.area_report_id}`}>Report #{ev.m1_context.area_report_id}</a>} />
                  <Fact label="Grid-cell score" value={ev.m1_context.cell_score != null ? ev.m1_context.cell_score.toFixed(1) : '-'} sub={ev.m1_context.cell_id ?? undefined} />
                  <Fact label="Hotspot" value={ev.m1_context.is_hotspot ? `Yes (#${ev.m1_context.hotspot_rank})` : 'No'} />
                  <Fact label="Nearest Savomart" value={ev.m1_context.nearest_savomart_m != null ? `${num(ev.m1_context.nearest_savomart_m)} m` : '-'} sub={ev.m1_context.nearest_savomart ?? undefined} />
                </div>
              ) : <p className="hint">This area has no completed M1 analysis yet. {ev ? '' : 'Context appears after evaluation.'}</p>}
            </section>

            <section className="card">
              <h2>Pipeline</h2>
              <ol className="timeline">
                {p.history.map((h, i) => (
                  <li key={i}>
                    <b>{STAGE_LABEL[h.to_stage as Stage] ?? h.to_stage}</b>
                    <div className="hint">{h.changed_by_name} · {fmtTime(h.changed_at)}</div>
                    {h.reason && <div className="tl-reason">“{h.reason}”</div>}
                    {h.evaluation_version != null && <div className="hint">Decided on evaluation v{h.evaluation_version}</div>}
                  </li>
                ))}
              </ol>
              <p className="hint">Catchment status: {afterCatchment ? (p.pipeline_stage === 'APPROVED' || p.pipeline_stage === 'REJECTED' ? 'study stage finished before the decision' : STAGE_LABEL[p.pipeline_stage]) : 'not requested'}</p>
            </section>

            {ev?.data_sources && (
              <section className="card sources">
                <h2>Data used</h2>
                <ul className="list-plain">
                  {ev.data_sources.map((s, i) => (
                    <li key={i}><b>{s.source}</b> {s.mocked && <span className="chip warn">estimated / mock</span>}<br />
                      <span className="hint">{s.fetched_at ? `Fetched ${fmtTime(s.fetched_at)}` : 'Not fetched live'}{s.note ? ` · ${s.note}` : ''}</span></li>
                  ))}
                </ul>
                {(p.evaluation_versions?.length ?? 0) > 1 && (
                  <p className="hint" style={{ marginTop: 8 }}>Evaluation history: {p.evaluation_versions.filter((v) => v.status === 'completed').map((v) => `v${v.version} = ${v.overall_score?.toFixed(1)}`).join(', ')}</p>
                )}
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
