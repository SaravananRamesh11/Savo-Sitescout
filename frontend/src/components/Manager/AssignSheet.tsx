import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { Hotspot } from '../../types'
import { Field } from '../common/ui'

/** Bottom sheet (phone) / modal (desktop): assign an M1 hotspot to a BD Executive. */
export default function AssignSheet({ hotspot, areaId, areaName, reportId, onClose }: {
  hotspot: Hotspot; areaId: number; areaName: string; reportId: number; onClose: () => void
}) {
  const [execs, setExecs] = useState<{ id: string; name: string }[]>([])
  const [exec, setExec] = useState('')
  const [notes, setNotes] = useState('')
  const [due, setDue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [doneFor, setDoneFor] = useState<string | null>(null)

  useEffect(() => { api.executives().then((e) => { setExecs(e); setExec(e[0]?.id ?? '') }).catch((e: ApiError) => setError(e.message)) }, [])

  const go = async () => {
    setBusy(true); setError(null)
    try {
      const a = await api.createAssignment({
        area_id: areaId, executive_id: exec, source_report_id: reportId, hotspot_cell_id: hotspot.cell_id,
        hotspot_lat: hotspot.lat, hotspot_lon: hotspot.lon, hotspot_label: `${hotspot.locality} (hotspot #${hotspot.rank})`,
        notes: notes.trim() || null, due_date: due || null,
      })
      setDoneFor(a.executive_name)
    } catch (e) { setError((e as ApiError).message) } finally { setBusy(false) }
  }

  return (
    <div className="modal-back" onClick={onClose} role="presentation">
      <div className="modal-sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Assign scouting">
        <div className="stack">
          <h2 style={{ fontSize: 18 }}>Assign scouting</h2>
          <p className="hint"><b>{hotspot.locality}</b> · hotspot #{hotspot.rank} in {areaName} (score {hotspot.score.toFixed(1)})</p>
          {doneFor ? (
            <>
              <div className="msg info">Assigned to <b>{doneFor}</b>. They will see it under My assignments.</div>
              <button className="btn block" onClick={onClose}>Done</button>
            </>
          ) : (
            <>
              <Field label="BD Executive">
                <select className="input" value={exec} onChange={(e) => setExec(e.target.value)}>
                  {execs.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </select>
              </Field>
              <Field label="Note for the executive" hint="What to look for, e.g. corner units above 1,500 sq ft">
                <textarea className="input" style={{ minHeight: 84, paddingTop: 10 }} value={notes} onChange={(e) => setNotes(e.target.value)} />
              </Field>
              <Field label="Due date (optional)"><input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
              {error && <div className="msg err" role="alert">{error}</div>}
              <div className="two-col">
                <button className="btn ghost" onClick={onClose}>Cancel</button>
                <button className="btn yellow" disabled={busy || !exec} onClick={go}>{busy ? <span className="loader" /> : null} Assign</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
