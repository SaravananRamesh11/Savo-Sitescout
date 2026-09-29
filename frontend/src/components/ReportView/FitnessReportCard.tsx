import type { ReportDetail } from '../../types'
import { FLAG_TEXT, fmtTime } from '../common/format'

export default function FitnessReportCard({ r }: { r: ReportDetail }) {
  const score = r.overall_score ?? 0
  const flags = r.data_quality_flags.filter((f) => FLAG_TEXT[f])
  return (
    <>
      <section className="score-hero" aria-label="Area fitness score">
        <div className="stack" style={{ gap: 8 }}>
          <div className="name">{r.area_name}</div>
          <div className="score-num">
            {score.toFixed(1)}
            <small> / 100</small>
          </div>
          <div>
            <span className="score-rating">{r.rating}</span>
          </div>
        </div>
        <div className="stack" style={{ gap: 10 }}>
          <div className="meter" role="img" aria-label={`Score ${score} out of 100`}>
            <i style={{ width: `${Math.min(score, 100)}%` }} />
          </div>
          <div className="score-meta">
            Report #{r.report_id} · generated {fmtTime(r.completed_at ?? r.created_at)}
            <br />
            {r.area_km2} km² analysed on a 500 m grid · explanation by {r.explanation_source === 'llm' ? 'AI (number-checked)' : 'fixed template'}
          </div>
        </div>
      </section>
      {flags.length > 0 && (
        <div className="stack" style={{ marginTop: 12 }}>
          {flags.map((f) => (
            <div key={f} className={`msg ${f === 'osm_mocked' ? 'err' : 'warn'}`}>{FLAG_TEXT[f]}</div>
          ))}
        </div>
      )}
      {r.explanation && (
        <section className="card" style={{ marginTop: 16 }}>
          <h2>Why this rating</h2>
          <p className="summary">{r.explanation.summary}</p>
          <ul className="list-plain" style={{ marginTop: 12 }}>
            {r.explanation.reasons.map((x, i) => (
              <li key={i}>{x.text}</li>
            ))}
          </ul>
          {r.explanation.caveats.length > 0 && (
            <p className="hint" style={{ marginTop: 10 }}>Caveats: {r.explanation.caveats.join(' · ')}</p>
          )}
        </section>
      )}
    </>
  )
}
