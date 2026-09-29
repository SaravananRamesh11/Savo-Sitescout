import type { ReportDetail } from '../../types'
import { fmtNum, fmtTime } from '../common/format'

const FACTS: [string, string, boolean?][] = [
  ['estimated_population', 'People (estimate)', true],
  ['estimated_households', 'Homes (estimate)', true],
  ['shops_offices_banks', 'Shops, offices, banks'],
  ['schools', 'Schools'],
  ['hospitals', 'Hospitals'],
  ['clinics', 'Clinics'],
  ['supermarkets', 'Supermarkets'],
  ['convenience_stores', 'Convenience stores'],
  ['transit_stops', 'Transit stops'],
  ['road_km', 'Road length (km)'],
]

export function AreaProfile({ r }: { r: ReportDetail }) {
  const p = r.profile
  return (
    <section className="card">
      <h2>What the area is like</h2>
      <div className="facts">
        {FACTS.map(([k, label]) => (
          <div className="fact" key={k}>
            <b>{fmtNum(p[k])}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>
      <p className="hint" style={{ marginTop: 12 }}>
        Nearest Savomart: <b>{String(p.nearest_savomart_name ?? 'none known')}</b>
        {p.nearest_savomart_m != null && <> · {fmtNum(p.nearest_savomart_m)} m from the area centre</>}. Stores inside the area:{' '}
        {fmtNum(p.savomart_stores_inside)}; within 3 km: {fmtNum(p.savomart_stores_within_3km)}.
      </p>
    </section>
  )
}

export function DataSources({ r }: { r: ReportDetail }) {
  return (
    <section className="card sources">
      <h2>Data used in this report</h2>
      <ul className="list-plain">
        {r.data_sources.map((s, i) => (
          <li key={i}>
            <b>{s.source}</b> {s.mocked && <span className="chip warn">estimated / mock</span>}
            <br />
            <span className="hint">
              {s.fetched_at ? `Fetched ${fmtTime(s.fetched_at)}` : 'Not fetched live'}
              {s.note ? ` · ${s.note}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
