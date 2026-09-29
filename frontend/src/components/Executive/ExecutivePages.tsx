import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useState } from 'react'
import { Circle, GeoJSON, MapContainer, Marker, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { api, ApiError } from '../../api/client'
import { MAP } from '../../theme/colors'
import type { Assignment, PropertySummary } from '../../types'
import { fmtTime } from '../common/format'
import { inr, StageChip } from '../common/ui'

function AutoResize() {
  const map = useMap()
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  return null
}

export function AssignmentList({ onOpen }: { onOpen: (id: number) => void }) {
  const [items, setItems] = useState<Assignment[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.assignments().then(setItems).catch((e: ApiError) => setError(e.message)) }, [])
  return (
    <div className="scroll-view">
      <div className="page stack">
        <div>
          <h1 style={{ fontSize: 22 }}>My assignments</h1>
          <p className="hint">Places your BD Manager wants you to scout. Open one, then add the properties you find.</p>
        </div>
        {error && <div className="msg err">{error}</div>}
        {!items && !error && <div className="empty"><span className="loader" /></div>}
        {items && items.length === 0 && <div className="empty card"><h2>Nothing assigned yet</h2><p>Your manager will assign a hotspot to you.</p></div>}
        {items?.map((a) => (
          <button key={a.assignment_id} className="card assign-card" onClick={() => onOpen(a.assignment_id)}>
            <div className="ac-top">
              <b>{a.hotspot_label ?? a.area_name}</b>
              <span className={`chip${a.status === 'OPEN' ? ' yellow' : ''}`}>{a.status === 'OPEN' ? 'To do' : a.status.toLowerCase()}</span>
            </div>
            <div className="hint">{a.hotspot_road ? `Near ${a.hotspot_road} · ` : ''}{a.area_name}{a.due_date ? ` · due ${a.due_date}` : ''}</div>
            {a.notes && <p className="ac-notes">“{a.notes}”</p>}
            <div className="hint">Assigned {fmtTime(a.created_at)} · {a.properties_captured} property(ies) captured</div>
          </button>
        ))}
      </div>
    </div>
  )
}

const pin = L.divIcon({ className: '', html: '<div class="mini-pin"></div>', iconSize: [18, 18], iconAnchor: [9, 9] })

function Fit({ a }: { a: Assignment }) {
  const map = useMap()
  useEffect(() => {
    if (a.hotspot_lat != null && a.hotspot_lon != null) map.setView([a.hotspot_lat, a.hotspot_lon], 16)
    else map.fitBounds(L.geoJSON(a.area_geometry).getBounds(), { padding: [20, 20] })
  }, [a, map])
  return null
}

/** Text pieces for "where to look", plus web links an executive (or tester) can use to find real buildings. */
function whereToLook(a: Assignment) {
  const place = a.hotspot_locality ?? a.hotspot_label ?? a.area_name
  const road = a.hotspot_road ?? null
  const query = [road, a.hotspot_locality, 'Chennai'].filter(Boolean).join(', ')
  const at = a.hotspot_lat != null && a.hotspot_lon != null ? `${a.hotspot_lat},${a.hotspot_lon}` : null
  return {
    place, road,
    google: `https://www.google.com/maps/search/${encodeURIComponent(`shops near ${query}`)}${at ? `/@${at},17z` : ''}`,
    osm: at ? `https://www.openstreetmap.org/?mlat=${a.hotspot_lat}&mlon=${a.hotspot_lon}#map=18/${a.hotspot_lat}/${a.hotspot_lon}` : null,
    coords: at,
  }
}

export function AssignmentDetail({ id, onBack, onAdd, onOpenProperty }: {
  id: number; onBack: () => void; onAdd: () => void; onOpenProperty: (id: number, stage: string) => void
}) {
  const [a, setA] = useState<Assignment | null>(null)
  const [props, setProps] = useState<PropertySummary[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.assignment(id).then(setA).catch((e: ApiError) => setError(e.message))
    api.properties().then((all) => setProps(all.filter((p) => p.assignment_id === id))).catch(() => undefined)
  }, [id])
  return (
    <div className="scroll-view">
      <div className="page stack">
        <button className="btn ghost sm" onClick={onBack}>← Assignments</button>
        {error && <div className="msg err">{error}</div>}
        {!a && !error && <div className="empty"><span className="loader" /></div>}
        {a && (
          <>
            <div>
              <h1 style={{ fontSize: 22 }}>{a.hotspot_label ?? a.area_name}</h1>
              <p className="hint">{a.area_name}{a.due_date ? ` · due ${a.due_date}` : ''}</p>
              {a.notes && <div className="msg info" style={{ marginTop: 8 }}>Manager's note: {a.notes}</div>}
            </div>
            <div className="loc-map" style={{ height: 240 }}>
              <MapContainer maxZoom={21} center={[13.0827, 80.2707]} zoom={12} style={{ height: '100%' }} scrollWheelZoom={false}>
                <TileLayer maxZoom={21} maxNativeZoom={19} attribution='&copy; OpenStreetMap contributors' url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <GeoJSON data={a.area_geometry} style={{ color: MAP.boundary, weight: 2, fillOpacity: 0.06 }} />
                {a.hotspot_lat != null && a.hotspot_lon != null && (
                  <Circle center={[a.hotspot_lat, a.hotspot_lon]} radius={150} pathOptions={{ color: '#782B90', fillColor: '#FFF200', fillOpacity: 0.4, weight: 2 }}>
                    <Tooltip permanent direction="top">Scout here: {a.hotspot_locality ?? a.hotspot_label ?? a.area_name}</Tooltip>
                  </Circle>
                )}
                {props.map((p) => (
                  <Marker key={p.property_id} position={[p.lat, p.lon]} icon={pin}><Tooltip>#{p.property_id} {p.address ?? ''}</Tooltip></Marker>
                ))}
                <Fit a={a} />
          <AutoResize />
              </MapContainer>
            </div>
            {(() => {
              const w = whereToLook(a)
              return (
                <section className="card where-card" aria-label="Where to look">
                  <h2>Where to look</h2>
                  <p><b>{w.place}</b>{w.road ? <> · near <b>{w.road}</b></> : null}</p>
                  <p className="hint">{a.area_name}{w.coords ? ` · ${w.coords.split(',').map((x) => Number(x).toFixed(5)).join(', ')}` : ''}</p>
                  <div className="two-col" style={{ marginTop: 10 }}>
                    <a className="btn ghost sm" href={w.google} target="_blank" rel="noreferrer">Find shops on Google Maps</a>
                    {w.osm && <a className="btn ghost sm" href={w.osm} target="_blank" rel="noreferrer">Open on OpenStreetMap</a>}
                  </div>
                  <p className="hint" style={{ marginTop: 8 }}>Pick a building you find there, then tap Add property and place the pin on it.</p>
                </section>
              )
            })()}
            <button className="btn yellow block" onClick={onAdd}>+ Add property</button>
            <section className="card">
              <h2>Captured for this assignment</h2>
              {props.length === 0 && <p className="hint">Nothing yet. Tap “Add property” when you are at a building.</p>}
              {props.map((p) => <PropertyRow key={p.property_id} p={p} onOpen={() => onOpenProperty(p.property_id, p.pipeline_stage)} />)}
            </section>
          </>
        )}
      </div>
    </div>
  )
}

export function PropertyRow({ p, onOpen }: { p: PropertySummary; onOpen: () => void }) {
  return (
    <button className="prop-row" onClick={onOpen}>
      <div className="pr-photo">{p.photo_url ? <img src={p.photo_url} alt="" /> : <span>No photo</span>}</div>
      <div className="pr-main">
        <b>{p.address ?? `Property #${p.property_id}`}</b>
        <div className="hint">{p.locality ?? p.area_name} · {inr(p.monthly_rent)}{p.rent_per_sqft ? ` (₹${p.rent_per_sqft}/sq ft)` : ''}</div>
        <div className="chips"><StageChip stage={p.pipeline_stage} sentBack={p.submitted_at != null} />{p.possible_duplicate && <span className="chip warn">possible duplicate</span>}</div>
      </div>
      <div className="mini-score">{p.evaluation?.overall_score != null ? p.evaluation.overall_score.toFixed(0) : p.evaluation_status === 'running' ? '…' : ''}</div>
    </button>
  )
}
