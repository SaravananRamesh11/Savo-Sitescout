import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Circle, GeoJSON, MapContainer, Marker, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import { api } from '../../api/client'
import { MAP } from '../../theme/colors'
import type { Assignment, Duplicate } from '../../types'
import { Field } from '../common/ui'

export type Loc = { lat: number; lon: number; accuracy: number | null; source: 'gps' | 'manual_pin' }

const pinIcon = L.divIcon({
  className: '',
  html: '<div class="drop-pin"><span></span></div>',
  iconSize: [34, 42],
  iconAnchor: [17, 40],
})

function Recenter({ loc, fallback }: { loc: Loc | null; fallback: [number, number] | null }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    if (loc) {
      map.setView([loc.lat, loc.lon], Math.max(map.getZoom(), 17), { animate: true })
      done.current = true
    } else if (fallback && !done.current) {
      map.setView(fallback, 16)
      done.current = true
    }
  }, [loc, fallback, map])
  return null
}

function ClickToPlace({ onPlace }: { onPlace: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onPlace(e.latlng.lat, e.latlng.lng) })
  return null
}

function geoMessage(err: GeolocationPositionError | { code: number }): string {
  if (!window.isSecureContext)
    return 'Location needs a secure (https) connection. Tap the map to place the pin instead.'
  if (err.code === 1) return 'Location permission was denied. Allow it in the browser settings, or tap the map to place the pin.'
  if (err.code === 2) return 'Your device could not work out its location. Tap the map to place the pin.'
  if (err.code === 3) return 'Getting your location took too long. Try again, or tap the map to place the pin.'
  return 'Could not get your location. Tap the map to place the pin.'
}

function AutoResize() {
  const map = useMap()
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  return null
}

export default function LocationStep({
  loc, setLoc, assignment, propertyId, address, locality, pincode, onPrefill, errors,
}: {
  loc: Loc | null
  setLoc: (l: Loc) => void
  assignment: Assignment | null
  propertyId: number | null
  address: string | undefined
  locality: string | undefined
  pincode: string | undefined
  onPrefill: (v: { address?: string; locality?: string; pincode?: string }) => void
  errors: Record<string, string>
}) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [dups, setDups] = useState<Duplicate[]>([])
  const timer = useRef<number | undefined>(undefined)
  const hotspot: [number, number] | null =
    assignment && assignment.hotspot_lat != null && assignment.hotspot_lon != null ? [assignment.hotspot_lat, assignment.hotspot_lon] : null

  // Debounced duplicate warning as soon as a pin is placed or moved.
  useEffect(() => {
    if (!loc) return
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      api
        .duplicateCheck({ lat: loc.lat, lon: loc.lon, exclude_property_id: propertyId })
        .then((r) => setDups(r.duplicates))
        .catch(() => setDups([]))
    }, 500)
    return () => window.clearTimeout(timer.current)
  }, [loc, propertyId])

  // Latest typed values, readable inside async callbacks without stale closures.
  const cur = useRef({ address, locality, pincode })
  cur.current = { address, locality, pincode }
  // What we last filled in automatically. A field is overwritten by a new lookup only if it is empty or still
  // equal to that value, so anything the executive typed themselves is never replaced.
  const auto = useRef<{ address?: string; locality?: string; pincode?: string }>({})
  const [lookup, setLookup] = useState<{ state: 'idle' | 'busy' | 'ok' | 'none'; text?: string }>({ state: 'idle' })
  const seq = useRef(0)

  const prefill = useCallback(
    (lat: number, lon: number) => {
      const mine = ++seq.current
      setLookup({ state: 'busy' })
      api
        .reverse(lat, lon)
        .then((r) => {
          if (mine !== seq.current) return // a newer pin was placed meanwhile
          const c = cur.current
          const canSet = (key: 'address' | 'locality' | 'pincode') => !c[key] || c[key] === auto.current[key]
          const v: { address?: string; locality?: string; pincode?: string } = {}
          if (r.address && canSet('address')) v.address = r.address
          if (r.locality && canSet('locality')) v.locality = r.locality
          if (r.pincode && /^\d{6}$/.test(r.pincode) && canSet('pincode')) v.pincode = r.pincode
          auto.current = { ...auto.current, ...v }
          if (Object.keys(v).length) onPrefill(v)
          if (r.address || r.locality || r.pincode)
            setLookup({ state: 'ok', text: [r.address, r.pincode].filter(Boolean).join(' · ') })
          else setLookup({ state: 'none' })
        })
        .catch(() => { if (mine === seq.current) setLookup({ state: 'none' }) })
    },
    [onPrefill],
  )

  const useCurrent = () => {
    setMsg(null)
    if (!('geolocation' in navigator)) {
      setMsg('This browser cannot share its location. Tap the map to place the pin.')
      return
    }
    if (!window.isSecureContext) {
      setMsg(geoMessage({ code: 0 }))
      return
    }
    setBusy(true)
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setBusy(false)
        const l: Loc = { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy, source: 'gps' }
        setLoc(l)
        prefill(l.lat, l.lon)
      },
      (e) => {
        setBusy(false)
        setMsg(geoMessage(e))
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    )
  }

  const place = (lat: number, lon: number) => {
    setMsg(null)
    setLoc({ lat, lon, accuracy: null, source: 'manual_pin' })
    prefill(lat, lon)
  }

  const weak = loc?.source === 'gps' && loc.accuracy != null && loc.accuracy > 100

  return (
    <div className="stack">
      <div className="msg info">
        Stand at the property and tap <b>Use current location</b>. Then drag the pin onto the building if needed. On a laptop the
        location can be off by a few hundred metres, so adjust the pin.
      </div>
      <button type="button" className="btn block" onClick={useCurrent} disabled={busy}>
        {busy ? <span className="loader" /> : '📍'} Use current location
      </button>
      {msg && <div className="msg warn" role="alert">{msg}</div>}
      <div className="loc-map" aria-label="Property location map">
        <MapContainer maxZoom={21} center={hotspot ?? [13.0827, 80.2707]} zoom={hotspot ? 16 : 11} style={{ height: '100%' }}>
          <TileLayer maxZoom={21} maxNativeZoom={19}
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {assignment && <GeoJSON data={assignment.area_geometry} style={{ color: MAP.boundary, weight: 2, fillOpacity: 0.05, dashArray: '6 6' }} />}
          {hotspot && (
            <Circle center={hotspot} radius={120} pathOptions={{ color: '#782B90', fillColor: '#FFF200', fillOpacity: 0.35, weight: 2 }}>
              <Tooltip permanent direction="top">Scout here: {assignment?.hotspot_locality ?? assignment?.hotspot_label}</Tooltip>
            </Circle>
          )}
          {loc && loc.accuracy != null && <Circle center={[loc.lat, loc.lon]} radius={loc.accuracy} pathOptions={{ color: '#782B90', weight: 1, fillOpacity: 0.1 }} />}
          {loc && (
            <Marker
              position={[loc.lat, loc.lon]}
              icon={pinIcon}
              draggable
              eventHandlers={{ dragend: (e) => { const ll = (e.target as L.Marker).getLatLng(); place(ll.lat, ll.lng) } }}
            />
          )}
          <ClickToPlace onPlace={place} />
          <AutoResize />
          <Recenter loc={loc} fallback={hotspot} />
        </MapContainer>
      </div>
      {loc ? (
        <p className="hint">
          {loc.source === 'gps' ? 'From device location' : 'Placed on the map'} · {loc.lat.toFixed(5)}, {loc.lon.toFixed(5)}
          {loc.accuracy != null && <> · accuracy about {Math.round(loc.accuracy)} m</>}
        </p>
      ) : (
        <p className="hint">No pin yet. Use your current location, or tap the map where the property is.</p>
      )}
      {lookup.state === 'busy' && <p className="hint"><span className="loader" style={{ width: 14, height: 14, borderWidth: 2 }} /> Looking up the address for this spot…</p>}
      {lookup.state === 'ok' && <p className="hint">Address found on the map: <b>{lookup.text}</b>. Check it and add the door number.</p>}
      {lookup.state === 'none' && <div className="msg warn">No address was found for this exact spot (or the address service is busy). Type the address below, or move the pin onto a street and it will try again.</div>}
      {weak && <div className="msg warn">Accuracy is low (about {Math.round(loc!.accuracy!)} m). Drag the pin onto the property to be exact.</div>}
      {errors.location && <div className="msg err" role="alert">{errors.location}</div>}
      {dups.length > 0 && (
        <div className="msg warn" role="alert">
          <b>{dups[0].message}</b>
          {dups.length > 1 && ` (and ${dups.length - 1} more nearby)`}. You can continue, but check it is not the same building.
          {dups.slice(0, 2).map((d) => (
            <div key={d.property_id}>
              <a href={`#/property/${d.property_id}`} target="_blank" rel="noreferrer">
                View property #{d.property_id}{d.address ? ` (${d.address})` : ''}
              </a>
            </div>
          ))}
        </div>
      )}
      <Field label="Address" required error={errors.address}>
        <input className="input" value={address ?? ''} placeholder="Door no., street" onChange={(e) => onPrefill({ address: e.target.value })} />
      </Field>
      <div className="two-col">
        <Field label="Locality" required error={errors.locality}>
          <input className="input" value={locality ?? ''} onChange={(e) => onPrefill({ locality: e.target.value })} />
        </Field>
        <Field label="Pincode" required error={errors.pincode}>
          <input className="input" inputMode="numeric" maxLength={6} value={pincode ?? ''} placeholder="600042" onChange={(e) => onPrefill({ pincode: e.target.value.replace(/\D/g, '') })} />
        </Field>
      </div>
    </div>
  )
}
