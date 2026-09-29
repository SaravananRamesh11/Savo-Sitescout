import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { Study, StudyRow } from '../../types'
import { fmtTime } from '../common/format'
import InsightsView from './InsightsView'

/** BD Manager: request a ground catchment study for a whole analysed area and see where it stands. */
export default function AreaCatchmentCard({ areaId }: { areaId: number }) {
  const [row, setRow] = useState<StudyRow | null | undefined>(undefined)
  const [full, setFull] = useState<Study | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(() => api.areaCatchment(areaId).then(setRow).catch(() => setRow(null)), [areaId])
  useEffect(() => { load() }, [load])
  const open = row && row.status !== 'COMPLETED' ? true : false

  const request = async () => {
    setBusy(true); setErr(null)
    try { await api.requestStudy({ area_id: areaId }); await load() } catch (e) { setErr((e as ApiError).message) } finally { setBusy(false) }
  }
  if (row === undefined) return null
  return (
    <section className="card stack" aria-label="Ground catchment survey for this area">
      <h2>Ground catchment survey</h2>
      {!row && <p className="hint">Send survey executives to walk this area and record what is really there. If a recent, good survey already covers it, it is reused instead.</p>}
      {row && (
        <>
          <div className="chips"><span className={`chip${row.status === 'COMPLETED' ? ' yellow' : ''}`}>{row.status.replace('_', ' ').toLowerCase()}</span>{row.reused && <span className="chip">reused survey</span>}</div>
          <p className="hint">Requested {fmtTime(row.requested_at)}{row.completed_at ? ` · completed ${fmtTime(row.completed_at)}` : ''}.</p>
          {row.reuse_reason && <div className="msg info">{row.reuse_reason}</div>}
        </>
      )}
      {err && <div className="msg err" role="alert">{err}</div>}
      {!open && <button className="btn block" disabled={busy} onClick={request}>{busy ? <span className="loader" /> : null} {row ? 'Request a new catchment study' : 'Request catchment study for this area'}</button>}
      {row?.status === 'COMPLETED' && !full && <button className="btn ghost block" onClick={() => api.study(row.study_id).then(setFull)}>View the findings</button>}
      {full?.insights && <InsightsView ins={full.insights} photos={full.evidence_photos} />}
    </section>
  )
}
