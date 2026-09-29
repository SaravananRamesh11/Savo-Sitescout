import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { ReportDetail, ReportStatus } from '../../types'
import FitnessReportCard from './FitnessReportCard'
import HotspotList from './HotspotList'
import { AreaProfile, DataSources } from './ProfileAndSources'
import ReportMap from './ReportMap'
import ScoreBreakdown from './ScoreBreakdown'

function Progress({ st, onRetry }: { st: ReportStatus; onRetry: () => void }) {
  const failed = st.status === 'failed'
  return (
    <section className="card" aria-live="polite">
      <h2>{failed ? 'The analysis stopped' : 'Analysing area…'}</h2>
      {!failed && <p className="hint">Public map data can take a minute or two the first time an area is analysed. You can leave this page and come back from Reports.</p>}
      <ol className="steps" style={{ marginTop: 8 }}>
        {st.steps.map((s, i) => (
          <li key={s.node}>
            <span className={`dot ${s.status}`}>{s.status === 'done' ? '✓' : s.status === 'failed' ? '!' : i + 1}</span>
            <div>
              <div style={{ fontWeight: s.status === 'running' ? 800 : 600 }}>{s.label}</div>
              {s.note && <div className="step-note">{s.note}</div>}
            </div>
          </li>
        ))}
      </ol>
      {failed && (
        <div className="stack" style={{ marginTop: 12 }}>
          <div className="msg err" role="alert">
            Failed at <b>{st.steps.find((s) => s.node === st.failed_node)?.label ?? st.failed_node}</b>: {st.error}
          </div>
          <button className="btn block" onClick={onRetry}>Retry (uses data already fetched)</button>
        </div>
      )}
    </section>
  )
}

export default function ReportPage({ reportId, onBack }: { reportId: number; onBack: () => void }) {
  const [status, setStatus] = useState<ReportStatus | null>(null)
  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const poll = useCallback(async () => {
    try {
      const st = await api.status(reportId)
      setStatus(st)
      setError(null)
      if (st.status === 'completed') {
        setDetail(await api.report(reportId))
      } else if (st.status !== 'failed') {
        timer.current = window.setTimeout(poll, 2000)
      }
    } catch (e) {
      setError((e as ApiError).message)
      timer.current = window.setTimeout(poll, 4000) // weak network: keep trying quietly
    }
  }, [reportId])

  useEffect(() => {
    setStatus(null)
    setDetail(null)
    poll()
    return () => window.clearTimeout(timer.current)
  }, [poll])

  const retry = async () => {
    try {
      await api.retry(reportId)
      setDetail(null)
      poll()
    } catch (e) {
      setError((e as ApiError).message)
    }
  }

  return (
    <div className="scroll-view">
      <div className="page stack">
        <button className="btn ghost sm" onClick={onBack}>← Reports</button>
        {error && <div className="msg warn">{error} Retrying…</div>}
        {!status && !error && <div className="empty"><span className="loader" /></div>}
        {status && !detail && <Progress st={status} onRetry={retry} />}
        {detail && (
          <div className="page-grid two">
            <div className="stack">
              <FitnessReportCard r={detail} />
              <HotspotList hotspots={detail.hotspots} reportId={detail.report_id} areaId={detail.area_id} areaName={detail.area_name} canAssign />
              <ScoreBreakdown factors={detail.score_breakdown} />
            </div>
            <div className="stack sticky-col">
              <ReportMap geometry={detail.area_geometry} hotspots={detail.hotspots} />
              <AreaProfile r={detail} />
              <DataSources r={detail} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
