import { useState } from 'react'
import type { EvidencePhoto, Insights } from '../../types'
import { Fact } from '../common/ui'

const cap = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
const pairs = (o?: Record<string, number>) =>
  o && Object.keys(o).length ? Object.entries(o).map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(', ') : '-'
const top = (o?: Record<string, number>) => (o && Object.keys(o).length ? Object.entries(o).sort((a, b) => b[1] - a[1])[0][0] : '-')
const FLAG: Record<string, string> = {
  insufficient_data: 'Too little survey data to score the ground indicator.',
  low_survey_coverage: 'Less than 70% of the planned observations were recorded.',
  reused_study: 'This survey was reused from an earlier study.',
  split_by_area_only: 'Road data was unavailable, so work was split by area only.',
}

function Insufficient({ text }: { text?: string }) {
  return <div className="msg warn">{text ?? 'Insufficient data'}</div>
}

/** Ground-survey findings, shown as-recorded. Nothing here is estimated: missing data is labelled as such. */
export default function InsightsView({ ins, photos, title = 'Ground survey findings' }: { ins: Insights; photos?: EvidencePhoto[]; title?: string }) {
  const [open, setOpen] = useState<string | null>(null)
  const c = ins.competition
  const flags = ins.data_quality_flags.filter((f) => FLAG[f])
  return (
    <div className="stack">
      <section className="card">
        <h2>{title}{ins.preview ? ' (preview, not saved)' : ins.version ? ` (version ${ins.version})` : ''}</h2>
        <div className="facts">
          <Fact label="Survey coverage" value={`${ins.coverage_percentage.toFixed(0)}%`} tone={ins.coverage_percentage < 70 ? 'warn' : undefined} />
          <Fact label="Ground indicator" value={ins.ground_fit_score != null ? `${ins.ground_fit_score.toFixed(0)} / 100` : 'Insufficient data'} sub="separate from the M2 score" />
          <Fact label="Competitors seen" value={c.sufficient ? c.competitors : 'Insufficient data'} sub={c.nearest_to_property_m != null ? `nearest ${c.nearest_to_property_m} m` : undefined} />
        </div>
        {flags.map((f) => <div key={f} className="msg warn" style={{ marginTop: 8 }}>{FLAG[f]}</div>)}
        <ul className="list-plain" style={{ marginTop: 12 }}>{ins.key_findings.map((f, i) => <li key={i}>{f}</li>)}</ul>
      </section>

      {ins.risks.length > 0 && (
        <section className="card">
          <h2>Ground-survey risks</h2>
          <ul className="list-plain">{ins.risks.map((r) => <li key={r.code}><span className={`chip sev-${r.severity}`}>{r.severity}</span> {r.text}</li>)}</ul>
        </section>
      )}

      <section className="card">
        <h2>What was observed</h2>
        <div className="obs-grid">
          <div>
            <h3>Residential</h3>
            {ins.residential.sufficient ? (
              <p className="hint">{ins.residential.independent_houses != null ? `${ins.residential.independent_houses} independent houses. ` : ''}
                {ins.residential.apartment_complexes != null ? `${ins.residential.apartment_complexes} apartment complexes. ` : ''}
                Activity: {pairs(ins.residential.activity_level)}. Construction: {pairs(ins.residential.construction_activity)}.</p>
            ) : <Insufficient />}
          </div>
          <div>
            <h3>Commercial</h3>
            {ins.commercial.sufficient ? (
              <p className="hint">{ins.commercial.businesses_total} businesses: {pairs(ins.commercial.businesses_by_kind)}. Activity most often {top(ins.commercial.activity_level)}.</p>
            ) : <Insufficient />}
          </div>
          <div>
            <h3>Traffic and footfall</h3>
            {ins.traffic.sufficient ? (
              <p className="hint">Pedestrians: {pairs(ins.traffic.pedestrian)}. Vehicles: {pairs(ins.traffic.vehicle)}. Periods: {pairs(ins.traffic.observation_periods)}. <em>Observed levels, not counted volumes.</em></p>
            ) : <Insufficient />}
          </div>
          <div>
            <h3>Accessibility and parking</h3>
            {ins.accessibility.sufficient ? (
              <p className="hint">Road: {pairs(ins.accessibility.road_condition)}. Entry/exit: {pairs(ins.accessibility.entry_exit)}. Parking: {pairs(ins.accessibility.parking)}.
                {ins.accessibility.median_road_width_ft ? ` Typical width ${ins.accessibility.median_road_width_ft} ft.` : ''}
                {' '}Obstructions {ins.accessibility.obstructions}, closures {ins.accessibility.road_closures}, difficult turns {ins.accessibility.difficult_turns}.</p>
            ) : <Insufficient />}
          </div>
          <div>
            <h3>Demand generators</h3>
            {ins.demand_generators.sufficient ? (
              <p className="hint">{ins.demand_generators.observations ? pairs(ins.demand_generators.by_kind) : 'None recorded during the survey'}
                {ins.demand_generators.named?.length ? `: ${ins.demand_generators.named.join(', ')}` : ''}.</p>
            ) : <Insufficient text={ins.demand_generators.status} />}
          </div>
          <div>
            <h3>Local conditions</h3>
            <p className="hint">{ins.accessibility.local_conditions?.observations ? pairs(ins.accessibility.local_conditions.by_condition) : 'None recorded'}
              {ins.accessibility.local_conditions?.high_severity?.length ? ` (high severity: ${ins.accessibility.local_conditions.high_severity.map(cap).join(', ')})` : ''}.</p>
          </div>
        </div>
        {c.sufficient && c.list?.length > 0 && (
          <>
            <h3 style={{ marginTop: 14 }}>Observed competitors</h3>
            <ul className="list-plain">
              {c.list.map((x: any, i: number) => (
                <li key={i}><b>{x.name}</b> · {cap(x.competitor_type)} · {x.size} · customer activity {x.customer_activity}
                  {x.distance_to_property_m != null ? ` · ${x.distance_to_property_m} m from the property` : ''}</li>
              ))}
            </ul>
          </>
        )}
      </section>

      {photos && photos.length > 0 && (
        <section className="card">
          <h2>Survey evidence</h2>
          <div className="gallery">
            {photos.map((p) => (
              <button key={p.photo_id} className="g-item" onClick={() => setOpen(p.url)}>
                <img src={p.url} alt={cap(p.photo_type)} loading="lazy" />
                <span>{cap(p.photo_type)}</span>
              </button>
            ))}
          </div>
          {open && <div className="lightbox" onClick={() => setOpen(null)} role="presentation"><img src={open} alt="Survey evidence" /></div>}
        </section>
      )}
    </div>
  )
}
