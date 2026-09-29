import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { PropertySummary } from '../../types'
import { PropertyRow } from '../Executive/ExecutivePages'
import { STAGE_LABEL } from '../common/ui'

const FILTERS: [string, string][] = [
  ['', 'All'], ['SUBMITTED', 'Submitted'], ['UNDER_REVIEW', 'Under review'], ['CATCHMENT_REQUESTED', 'Catchment requested'],
  ['CATCHMENT_IN_PROGRESS', 'Catchment in progress'], ['CATCHMENT_COMPLETED', 'Catchment completed'],
  ['FINAL_REVIEW', 'Final review'], ['APPROVED', 'Approved'], ['REJECTED', 'Rejected'],
]

export default function PropertyList({ onOpen, mine }: { onOpen: (id: number) => void; mine?: boolean }) {
  const [stage, setStage] = useState('')
  const [items, setItems] = useState<PropertySummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setItems(null)
    const load = () => api.properties(stage || undefined).then((r) => { setItems(r); setError(null) }).catch((e: ApiError) => setError(e.message))
    load()
    const t = window.setInterval(() => { if (items?.some((i) => i.evaluation_status === 'running')) load() }, 4000)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage])

  const counts = (items ?? []).reduce<Record<string, number>>((m, p) => ({ ...m, [p.pipeline_stage]: (m[p.pipeline_stage] ?? 0) + 1 }), {})
  return (
    <div className="scroll-view">
      <div className="page stack">
        <div>
          <h1 style={{ fontSize: 22 }}>{mine ? 'My properties' : 'Property pipeline'}</h1>
          <p className="hint">{mine ? 'Everything you have captured.' : 'Properties scouted by your executives, from capture to decision.'}</p>
        </div>
        {!mine && (
          <div className="filter-row" role="group" aria-label="Filter by stage">
            {FILTERS.map(([v, l]) => (
              <button key={v} className={`pill${stage === v ? ' on' : ''}`} onClick={() => setStage(v)} aria-pressed={stage === v}>
                {l}{stage === '' && v && counts[v] ? ` (${counts[v]})` : ''}
              </button>
            ))}
          </div>
        )}
        {error && <div className="msg err">{error}</div>}
        {!items && !error && <div className="empty"><span className="loader" /></div>}
        {items && items.length === 0 && (
          <div className="empty card"><h2>No properties {stage ? `in “${STAGE_LABEL[stage as keyof typeof STAGE_LABEL]}”` : 'yet'}</h2>
            <p>{mine ? 'Add one from an assignment.' : 'Assign a hotspot to an executive from any report to start scouting.'}</p></div>
        )}
        {items && items.length > 0 && (
          <section className="card">{items.map((p) => <PropertyRow key={p.property_id} p={p} onOpen={() => onOpen(p.property_id)} />)}</section>
        )}
      </div>
    </div>
  )
}
