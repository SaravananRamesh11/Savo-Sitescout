import type { PropertyCatchment } from '../../types'
import { fmtTime } from '../common/format'
import InsightsView from '../Survey/InsightsView'

const TEXT: Record<string, string> = {
  REQUESTED: 'Requested: waiting for the survey manager to plan the work',
  IN_PROGRESS: 'Survey in progress: executives are collecting observations on the ground',
  COMPLETED: 'Completed: findings below were recorded by survey executives on the ground',
}

/** Catchment-level view for the BD Manager: status, reuse note, and (when completed) the ground-survey findings. */
export default function CatchmentPanel({ c }: { c: PropertyCatchment }) {
  return (
    <div className="stack">
      <section className="card" aria-label="Catchment survey status">
        <h2>Ground catchment survey</h2>
        <div className="chips"><span className={`chip${c.status === 'COMPLETED' ? ' yellow' : ''}`}>{c.status.replace('_', ' ').toLowerCase()}</span>{c.reused && <span className="chip">reused survey</span>}</div>
        <p className="hint" style={{ marginTop: 8 }}>{TEXT[c.status]}.</p>
        <dl className="review-list" style={{ marginTop: 8 }}>
          <div><dt>Requested</dt><dd>{fmtTime(c.requested_at)}</dd></div>
          {c.started_at && !c.reused && <div><dt>Started</dt><dd>{fmtTime(c.started_at)}</dd></div>}
          {c.completed_at && <div><dt>{c.reused ? 'Reused on' : 'Completed'}</dt><dd>{fmtTime(c.completed_at)}</dd></div>}
          {c.survey_age_days != null && <div><dt>Survey age</dt><dd>{c.survey_age_days} day(s)</dd></div>}
        </dl>
        {c.reused && c.reuse_reason && <div className="msg info" style={{ marginTop: 8 }}>{c.reuse_reason} No new fieldwork was needed.</div>}
      </section>
      {c.insights && <InsightsView ins={c.insights} photos={c.evidence_photos} />}
    </div>
  )
}
