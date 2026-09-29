import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect } from 'react'
import { GeoJSON, MapContainer, Marker, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { MAP } from '../../theme/colors'
import type { Hotspot } from '../../types'

function Fit({ geometry }: { geometry: GeoJSON.Polygon }) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds(L.geoJSON(geometry).getBounds(), { padding: [20, 20] })
  }, [geometry, map])
  return null
}

const pin = (rank: number) =>
  L.divIcon({
    className: '',
    html: `<div style="width:30px;height:30px;border-radius:50%;background:#FFF200;color:#4f1a5f;border:3px solid #782B90;display:grid;place-items:center;font-weight:800;font-size:14px">${rank}</div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
  })

// Boundary + numbered hotspot pins only (no per-cell rendering in M1).
function AutoResize() {
  const map = useMap()
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  return null
}

export default function ReportMap({ geometry, hotspots }: { geometry: GeoJSON.Polygon; hotspots: Hotspot[] }) {
  return (
    <div className="report-map">
      <MapContainer maxZoom={21} center={[13.0827, 80.2707]} zoom={13} style={{ height: '100%' }} scrollWheelZoom={false}>
        <TileLayer maxZoom={21} maxNativeZoom={19}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <GeoJSON data={geometry} style={{ color: MAP.boundary, weight: 3, fillOpacity: 0.1 }} />
        {hotspots.map((h) => (
          <Marker key={h.cell_id} position={[h.lat, h.lon]} icon={pin(h.rank)}>
            <Tooltip>#{h.rank} {h.locality}</Tooltip>
          </Marker>
        ))}
        <Fit geometry={geometry} />
          <AutoResize />
      </MapContainer>
    </div>
  )
}
