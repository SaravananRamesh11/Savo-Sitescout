import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Circle, GeoJSON, MapContainer, Marker, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import { api, ApiError } from '../../api/client'
import type { CaptureType, UnitDetail } from '../../types'
import { Choice, Field, NumberInput, YesNo } from '../common/ui'
import { compressImage } from '../PropertyCapture/imageUtils'

type F = { key: string; label: string; kind: 'level' | 'choice' | 'int' | 'float' | 'bool' | 'text'; options?: [string, string][]; required?: boolean; hint?: string; unit?: string }
const LEVEL: [string, string][] = [['low', 'Low'], ['medium', 'Medium'], ['high', 'High']]
const SIZE: [string, string][] = [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']]

export const FORMS: Record<CaptureType, { label: string; help: string; photo: string; fields: F[] }> = {
  residential: {
    label: 'Homes', help: 'Houses and apartments you can see from here.', photo: 'other', fields: [
      { key: 'independent_houses', label: 'Independent houses seen', kind: 'int' },
      { key: 'apartment_complexes', label: 'Apartment complexes seen', kind: 'int' },
      { key: 'buildings_visible', label: 'Other buildings visible', kind: 'int' },
      { key: 'activity_level', label: 'Residential activity', kind: 'level', required: true, hint: 'People around, lights, vehicles at homes' },
      { key: 'occupancy', label: 'Occupancy (if you can tell)', kind: 'choice', options: [['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['unclear', 'Unclear']] },
      { key: 'construction_activity', label: 'Construction nearby', kind: 'choice', options: [['none', 'None'], ['some', 'Some'], ['heavy', 'Heavy']] },
      { key: 'notes', label: 'Notes', kind: 'text' }] },
  commercial: {
    label: 'Shops', help: 'A shop or business you see.', photo: 'commercial', fields: [
      { key: 'business_kind', label: 'Kind of business', kind: 'choice', required: true, options: [['grocery', 'Grocery'], ['supermarket', 'Supermarket'], ['convenience', 'Convenience'], ['pharmacy', 'Pharmacy'], ['other', 'Other']] },
      { key: 'name', label: 'Name (if visible)', kind: 'text' },
      { key: 'count', label: 'How many like this here', kind: 'int', hint: 'Leave 1 for a single shop' },
      { key: 'activity_level', label: 'Customer activity', kind: 'level', required: true },
      { key: 'notes', label: 'Notes', kind: 'text' }] },
  competition: {
    label: 'Competitor', help: 'A grocery competitor. Pin it where the shop is.', photo: 'competitor_evidence', fields: [
      { key: 'name', label: 'Name (if visible)', kind: 'text' },
      { key: 'competitor_type', label: 'Type', kind: 'choice', required: true, options: [['supermarket', 'Supermarket'], ['organised_grocery', 'Organised grocery'], ['convenience', 'Convenience'], ['kirana', 'Kirana'], ['other', 'Other']] },
      { key: 'size', label: 'Size', kind: 'choice', required: true, options: SIZE },
      { key: 'customer_activity', label: 'Customers seen', kind: 'level', required: true },
      { key: 'notes', label: 'Notes', kind: 'text' }] },
  traffic: {
    label: 'Footfall', help: 'Levels you observed. Do not estimate counts.', photo: 'other', fields: [
      { key: 'pedestrian', label: 'Pedestrian activity', kind: 'level', required: true },
      { key: 'vehicle', label: 'Vehicle activity', kind: 'level', required: true },
      { key: 'observation_period', label: 'When did you observe', kind: 'choice', required: true, options: [['morning', 'Morning'], ['midday', 'Midday'], ['evening', 'Evening'], ['night', 'Night']] },
      { key: 'observation_minutes', label: 'Minutes observed', kind: 'int' },
      { key: 'peak_notes', label: 'Peak activity you noticed', kind: 'text' }] },
  accessibility: {
    label: 'Access', help: 'The lane or road here.', photo: 'road_condition', fields: [
      { key: 'road_condition', label: 'Road condition', kind: 'choice', required: true, options: [['good', 'Good'], ['fair', 'Fair'], ['poor', 'Poor']] },
      { key: 'approx_road_width_ft', label: 'Approx. road width', kind: 'float', unit: 'ft' },
      { key: 'entry_exit', label: 'Entry and exit', kind: 'choice', required: true, options: [['easy', 'Easy'], ['moderate', 'Moderate'], ['difficult', 'Difficult']] },
      { key: 'parking', label: 'Parking', kind: 'choice', required: true, options: [['easy', 'Easy'], ['moderate', 'Moderate'], ['difficult', 'Difficult'], ['none', 'None']] },
      { key: 'obstruction', label: 'Obstruction', kind: 'bool' }, { key: 'construction', label: 'Construction on the road', kind: 'bool' },
      { key: 'road_closure', label: 'Road closure', kind: 'bool' }, { key: 'median_barrier', label: 'Median or barrier', kind: 'bool' },
      { key: 'difficult_turns', label: 'Difficult turns', kind: 'bool' }, { key: 'notes', label: 'Notes', kind: 'text' }] },
  demand_generator: {
    label: 'Demand', help: 'Something that draws people here.', photo: 'other', fields: [
      { key: 'kind', label: 'What is it', kind: 'choice', required: true, options: [['school', 'School'], ['college', 'College'], ['hospital', 'Hospital'], ['apartment_complex', 'Apartments'], ['office_cluster', 'Offices'], ['market', 'Market'], ['transit', 'Transit'], ['other', 'Other']] },
      { key: 'name', label: 'Name (if visible)', kind: 'text' },
      { key: 'size', label: 'Size', kind: 'choice', options: SIZE }, { key: 'notes', label: 'Notes', kind: 'text' }] },
  local_condition: {
    label: 'Condition', help: 'Anything unusual that affects the area.', photo: 'obstruction', fields: [
      { key: 'condition', label: 'What is it', kind: 'choice', required: true, options: [['construction', 'Construction'], ['vacant_land', 'Vacant land'], ['waterlogging', 'Waterlogging'], ['blocked_road', 'Blocked road'], ['parking_restriction', 'Parking restriction'], ['temporary_barrier', 'Temporary barrier'], ['other', 'Other']] },
      { key: 'severity', label: 'Severity', kind: 'level', required: true }, { key: 'notes', label: 'Notes', kind: 'text' }] },
}
export const TYPES = Object.keys(FORMS) as CaptureType[]
const PHOTO_TYPES: [string, string][] = [['competitor_evidence', 'Competitor'], ['road_condition', 'Road'], ['parking', 'Parking'], ['obstruction', 'Obstruction'], ['commercial', 'Shop'], ['other', 'Other']]

const pin = L.divIcon({ className: '', html: '<div class="drop-pin"><span></span></div>', iconSize: [34, 42], iconAnchor: [17, 40] })
const dot = L.divIcon({ className: '', html: '<div class="mini-pin"></div>', iconSize: [16, 16], iconAnchor: [8, 8] })

function inRing(x: number, y: number, ring: number[][]) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
export const insideUnit = (g: GeoJSON.MultiPolygon, lat: number, lon: number) =>
  g.coordinates.some((poly) => inRing(lon, lat, poly[0]) && !poly.slice(1).some((h) => inRing(lon, lat, h)))

function Click({ onPlace }: { onPlace: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onPlace(e.latlng.lat, e.latlng.lng) })
  return null
}
function FitTo({ geom, at }: { geom: GeoJSON.GeoJsonObject; at: { lat: number; lon: number } | null }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  useEffect(() => {
    if (at) map.setView([at.lat, at.lon], Math.max(map.getZoom(), 18))
    else if (!done.current) { map.fitBounds(L.geoJSON(geom).getBounds(), { padding: [16, 16] }); done.current = true }
  }, [at, geom, map])
  return null
}

export default function CaptureSheet({ unit, onClose, onSaved }: { unit: UnitDetail; onClose: () => void; onSaved: () => void }) {
  const [type, setType] = useState<CaptureType | null>(null)
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [loc, setLoc] = useState<{ lat: number; lon: number; accuracy: number | null } | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [locBusy, setLocBusy] = useState(false)
  const [photo, setPhoto] = useState<File | null>(null)
  const [photoType, setPhotoType] = useState('other')
  const [missing, setMissing] = useState<string[]>([])
  const spec = type ? FORMS[type] : null

  useEffect(() => { if (type) { setVals({ ...(type === 'commercial' ? { count: 1 } : {}) }); setPhotoType(FORMS[type].photo); setMissing([]); setErr(null) } }, [type])

  const useCurrent = () => {
    setMsg(null)
    if (!('geolocation' in navigator) || !window.isSecureContext) {
      setMsg(!window.isSecureContext ? 'Location needs a secure (https) connection. Tap the map to place the pin instead.' : 'This browser cannot share its location. Tap the map to place the pin.')
      return
    }
    setLocBusy(true)
    navigator.geolocation.getCurrentPosition(
      (p) => { setLocBusy(false); setLoc({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }) },
      (e) => { setLocBusy(false); setMsg(e.code === 1 ? 'Location permission was denied. Allow it in the browser, or tap the map to place the pin.' : 'Could not get your location. Tap the map to place the pin.') },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 })
  }

  const outside = loc != null && !insideUnit(unit.geometry, loc.lat, loc.lon)
  const set = (k: string, v: unknown) => { setVals((o) => ({ ...o, [k]: v })); setMissing((m) => m.filter((x) => x !== k)) }

  const save = async () => {
    if (!type || !spec) return
    const need = spec.fields.filter((f) => f.required && (vals[f.key] === undefined || vals[f.key] === '' || vals[f.key] === null)).map((f) => f.key)
    if (need.length) { setMissing(need); setErr('Fill the fields marked required.'); return }
    if (!loc) { setErr('Set where you observed this: use your location or tap the map.'); return }
    const data: Record<string, unknown> = {}
    for (const f of spec.fields) {
      const v = vals[f.key]
      if (v === undefined || v === '' || v === null) continue
      data[f.key] = v
    }
    setBusy(true); setErr(null)
    try {
      const cap = await api.addCapture(unit.unit_id, { capture_type: type, data, lat: loc.lat, lon: loc.lon, accuracy: loc.accuracy })
      if (photo) {
        try {
          await api.addCapturePhoto(cap.capture_id, photoType, await compressImage(photo), loc)
        } catch (e) {
          setMsg(`Saved, but the photo could not be uploaded (${(e as Error).message}). Add it again from the list.`)
        }
      }
      onSaved()
    } catch (e) {
      setErr((e as ApiError).message)
    } finally { setBusy(false) }
  }

  const center = useMemo(() => L.geoJSON(unit.geometry).getBounds().getCenter(), [unit.geometry])
  return (
    <div className="modal-back" role="presentation">
      <div className="modal-sheet tall" role="dialog" aria-modal="true" aria-label="Add observation">
        <div className="stack">
          <div className="sheet-title">
            <h2 style={{ fontSize: 18 }}>{spec ? spec.label : 'What did you observe?'}</h2>
            <button className="btn ghost sm" onClick={onClose}>Close</button>
          </div>
          {!type && (
            <div className="type-grid">
              {TYPES.map((t) => (
                <button key={t} className="type-btn" onClick={() => setType(t)}>
                  <b>{FORMS[t].label}</b><span>{FORMS[t].help}</span>
                </button>
              ))}
            </div>
          )}
          {type && spec && (
            <>
              <p className="hint">{spec.help}</p>
              {spec.fields.map((f) => (
                <Field key={f.key} label={f.label} required={f.required} hint={f.hint} error={missing.includes(f.key) ? 'Required' : undefined}>
                  {f.kind === 'level' && <Choice value={vals[f.key] as string} options={LEVEL} onChange={(v) => set(f.key, v)} />}
                  {f.kind === 'choice' && <Choice value={vals[f.key] as string} options={f.options!} onChange={(v) => set(f.key, v)} />}
                  {f.kind === 'bool' && <YesNo value={vals[f.key] as boolean | undefined} onChange={(v) => set(f.key, v)} />}
                  {(f.kind === 'int' || f.kind === 'float') && <NumberInput value={vals[f.key] as number | undefined} step={f.kind === 'int' ? '1' : 'any'} suffix={f.unit} onChange={(v) => set(f.key, v)} />}
                  {f.kind === 'text' && <input className="input" value={(vals[f.key] as string) ?? ''} maxLength={300} onChange={(e) => set(f.key, e.target.value)} />}
                </Field>
              ))}

              <h3>Where</h3>
              <button className="btn block" onClick={useCurrent} disabled={locBusy}>{locBusy ? <span className="loader" /> : '📍'} Use current location</button>
              {msg && <div className="msg warn" role="alert">{msg}</div>}
              <div className="loc-map" style={{ height: 230 }}>
                <MapContainer center={[center.lat, center.lng]} zoom={16} maxZoom={21} style={{ height: '100%' }}>
                  <TileLayer maxZoom={21} maxNativeZoom={19} attribution="&copy; OpenStreetMap contributors" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  <GeoJSON data={unit.geometry} style={{ color: '#782B90', weight: 3, fillOpacity: 0.08 }} />
                  {unit.captures.map((c) => <Marker key={c.capture_id} position={[c.lat, c.lon]} icon={dot} />)}
                  {loc && loc.accuracy != null && <Circle center={[loc.lat, loc.lon]} radius={loc.accuracy} pathOptions={{ color: '#782B90', weight: 1, fillOpacity: 0.1 }} />}
                  {loc && <Marker position={[loc.lat, loc.lon]} icon={pin} draggable eventHandlers={{ dragend: (e) => { const ll = (e.target as L.Marker).getLatLng(); setLoc({ lat: ll.lat, lon: ll.lng, accuracy: null }) } }} />}
                  <Click onPlace={(lat, lon) => setLoc({ lat, lon, accuracy: null })} />
                  <FitTo geom={unit.geometry} at={loc} />
                </MapContainer>
              </div>
              {loc ? <p className="hint">{loc.lat.toFixed(5)}, {loc.lon.toFixed(5)}{loc.accuracy != null ? ` · accuracy about ${Math.round(loc.accuracy)} m` : ' · placed on the map'}</p> : <p className="hint">Tap the map or use your location. Purple outline = your work unit {unit.unit_code}.</p>}
              {outside && <div className="msg warn">This pin is outside your work unit. Move it inside the purple outline, or the server will refuse it.</div>}

              <h3>Photo (optional)</h3>
              <div className="two-col">
                <label className="btn ghost sm" style={{ cursor: 'pointer' }}>
                  {photo ? 'Change photo' : '📷 Take / choose'}
                  <input type="file" accept="image/*" capture="environment" hidden onChange={(e) => { setPhoto(e.target.files?.[0] ?? null); e.target.value = '' }} />
                </label>
                <select className="input" style={{ minHeight: 40 }} value={photoType} onChange={(e) => setPhotoType(e.target.value)} aria-label="Photo type">
                  {PHOTO_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
              {photo && <p className="hint">Attached: {photo.name} <button className="linklike" onClick={() => setPhoto(null)}>remove</button></p>}

              {err && <div className="msg err" role="alert">{err}</div>}
              <div className="two-col">
                <button className="btn ghost" onClick={() => setType(null)} disabled={busy}>Change type</button>
                <button className="btn yellow" onClick={save} disabled={busy}>{busy ? <span className="loader" /> : null} Save observation</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
