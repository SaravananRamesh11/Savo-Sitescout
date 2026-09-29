import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useCallback, useEffect, useState } from 'react'
import { GeoJSON, MapContainer, Marker, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { api, ApiError } from '../../api/client'
import type { UnitDetail, WorkUnit } from '../../types'
import { compressImage } from '../PropertyCapture/imageUtils'
import CaptureSheet, { FORMS } from './CaptureSheet'

const dot = L.divIcon({ className: '', html: '<div class="mini-pin"></div>', iconSize: [16, 16], iconAnchor: [8, 8] })
const ST: Record<string, string> = { ASSIGNED: 'To do', IN_PROGRESS: 'In progress', COMPLETED: 'Completed' }

export function UnitList({ onOpen }: { onOpen: (id: number) => void }) {
  const [items, setItems] = useState<(WorkUnit & { study_id: number; label: string; locality: string | null })[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.surveyUnits().then(setItems).catch((e: ApiError) => setError(e.message)) }, [])
  return (
    <div className="scroll-view">
      <div className="page stack">
        <div>
          <h1 style={{ fontSize: 22 }}>My work units</h1>
          <p className="hint">Walk your area lane by lane and record what you see. You only see the units assigned to you.</p>
        </div>
        {error && <div className="msg err">{error}</div>}
        {!items && !error && <div className="empty"><span className="loader" /></div>}
        {items && items.length === 0 && <div className="empty card"><h2>No units yet</h2><p>Your survey manager will assign a work unit to you.</p></div>}
        {items?.map((u) => (
          <button key={u.unit_id} className="card assign-card" onClick={() => onOpen(u.unit_id)}>
            <div className="ac-top"><b>{u.unit_code} · {u.label}</b><span className={`chip${u.status === 'ASSIGNED' ? ' yellow' : ''}`}>{ST[u.status]}</span></div>
            <div className="hint">{u.locality ?? ''}{u.estimated_distance_m != null ? ` · ${(u.estimated_distance_m / 1000).toFixed(1)} km of lanes` : ''}</div>
            <div className="meter dark" aria-hidden><i style={{ width: `${Math.min(100, ((u.completed_capture_count) / Math.max(u.target_capture_count ?? 1, 1)) * 100)}%` }} /></div>
            <div className="hint">{u.completed_capture_count} of about {u.target_capture_count} observations{u.workload?.lanes?.length ? ` · ${u.workload.lanes.slice(0, 3).map((l) => l.name).join(', ')}` : ''}</div>
          </button>
        ))}
      </div>
    </div>
  )
}

function Fit({ geom }: { geom: GeoJSON.GeoJsonObject }) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds(L.geoJSON(geom).getBounds(), { padding: [16, 16] })
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [geom, map])
  return null
}

export function UnitDetailPage({ id, onBack }: { id: number; onBack: () => void }) {
  const [u, setU] = useState<UnitDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const load = useCallback(() => api.surveyUnit(id).then((r) => { setU(r); setError(null) }).catch((e: ApiError) => setError(e.message)), [id])
  useEffect(() => { load() }, [load])

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null); setNote(null)
    try { await fn(); await load() } catch (e) { setError((e as ApiError).message) } finally { setBusy(false) }
  }
  if (!u) return <div className="scroll-view"><div className="page stack"><button className="btn ghost sm" onClick={onBack}>← My units</button>{error ? <div className="msg err">{error}</div> : <div className="empty"><span className="loader" /></div>}</div></div>
  const done = u.status === 'COMPLETED'
  return (
    <div className="scroll-view">
      <div className="page stack">
        <button className="btn ghost sm" onClick={onBack}>← My units</button>
        <div>
          <h1 style={{ fontSize: 22 }}>{u.unit_code} · {u.label}</h1>
          <div className="chips" style={{ marginTop: 6 }}><span className={`chip${u.status === 'ASSIGNED' ? ' yellow' : ''}`}>{ST[u.status]}</span>
            <span className="chip">{u.completed_capture_count}/{u.target_capture_count} observations</span></div>
        </div>
        {error && <div className="msg err" role="alert">{error}</div>}
        {note && <div className="msg info">{note}</div>}
        <div className="loc-map" style={{ height: 280 }}>
          <MapContainer center={[13.08, 80.27]} zoom={16} maxZoom={21} style={{ height: '100%' }}>
            <TileLayer maxZoom={21} maxNativeZoom={19} attribution="&copy; OpenStreetMap contributors" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
            <GeoJSON data={u.geometry} style={{ color: '#782B90', weight: 3, fillColor: '#FFF200', fillOpacity: 0.18 }} />
            {u.captures.map((c) => <Marker key={c.capture_id} position={[c.lat, c.lon]} icon={dot}><Tooltip>{FORMS[c.capture_type].label}</Tooltip></Marker>)}
            <Fit geom={u.geometry} />
          </MapContainer>
        </div>
        <section className="card">
          <h2>Cover this ground</h2>
          <p className="hint">Estimated {u.estimated_distance_m != null ? `${(u.estimated_distance_m / 1000).toFixed(1)} km of lanes` : 'area only (no road data)'} in this unit.</p>
          {u.workload?.lanes?.length ? <ul className="list-plain" style={{ marginTop: 8 }}>{u.workload.lanes.map((l) => <li key={l.name}>{l.name} <span className="hint">· about {l.length_m} m here</span></li>)}</ul> : null}
        </section>

        {!done && (
          <div className="stack">
            {u.status === 'ASSIGNED' && <button className="btn block" disabled={busy} onClick={() => run(() => api.startUnit(id))}>Start this unit</button>}
            <button className="btn yellow block" onClick={() => setAdding(true)}>+ Add observation</button>
          </div>
        )}
        {done && <div className="msg info">This unit is completed. Observations are locked.</div>}

        <section className="card">
          <h2>My observations ({u.captures.length})</h2>
          {u.captures.length === 0 && <p className="hint">Nothing recorded yet.</p>}
          {u.captures.map((c) => (
            <div className="cap-row" key={c.capture_id}>
              <div>
                <b>{FORMS[c.capture_type].label}</b>
                <div className="hint">{Object.entries(c.data).filter(([k]) => k !== 'notes').slice(0, 4).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'boolean' ? (v ? 'yes' : 'no') : v}`).join(' · ')}</div>
                {c.photos.length > 0 && <div className="thumbs">{c.photos.map((p) => <div className="thumb" key={p.photo_id}><img src={p.url} alt="Evidence" /></div>)}</div>}
              </div>
              {!done && (
                <div className="cap-actions">
                  <label className="btn ghost sm" style={{ cursor: 'pointer' }}>📷
                    <input type="file" accept="image/*" capture="environment" hidden onChange={(e) => {
                      const f = e.target.files?.[0]; e.target.value = ''
                      if (f) run(async () => api.addCapturePhoto(c.capture_id, FORMS[c.capture_type].photo, await compressImage(f), { lat: c.lat, lon: c.lon, accuracy: c.accuracy_m }))
                    }} />
                  </label>
                  <button className="btn ghost sm" disabled={busy} onClick={() => { if (window.confirm('Delete this observation?')) run(() => api.deleteCapture(c.capture_id)) }}>Delete</button>
                </div>
              )}
            </div>
          ))}
        </section>

        {!done && (
          <button className="btn block" disabled={busy || u.completed_capture_count < 1} onClick={() => run(async () => { const r = await api.completeUnit(id); if (r.note) setNote(r.note) })}>
            Mark this unit complete
          </button>
        )}
        {!done && u.completed_capture_count < 1 && <p className="hint">Record at least one observation before you can complete this unit.</p>}
      </div>
      {adding && <CaptureSheet unit={u} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load() }} />}
    </div>
  )
}
