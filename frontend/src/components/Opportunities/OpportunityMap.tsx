import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { CircleMarker, GeoJSON, MapContainer, Marker, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import { MAP, OPP } from '../../theme/colors'
import type { OppCell } from '../../types'

export type OppMode = 'opportunity' | 'coverage'
type Props = {
  runId: number
  cells: OppCell[]
  mode: OppMode
  selectedId: string | null
  top: { rank: number; cell_id: string; lat: number; lon: number; locality?: string; opportunity_score?: number }[]
  stores: { name: string; lat: number; lon: number }[]
  onSelect: (id: string) => void
}

// Pins shrink when zoomed out, where the best cells crowd together; the ranked list below is the reliable index.
const rankIcon = (rank: number, zoom: number) => {
  const size = zoom >= 14 ? 30 : zoom >= 12 ? 24 : 18
  return L.divIcon({
    className: '',
    html: `<div class="opp-pin" style="width:${size}px;height:${size}px;font-size:${size >= 24 ? 13 : 10}px">${rank}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

function ZoomWatcher({ onZoom }: { onZoom: (z: number) => void }) {
  const map = useMap()
  useEffect(() => onZoom(map.getZoom()), [map, onZoom])
  useMapEvents({ zoomend: () => onZoom(map.getZoom()) })
  return null
}

/** Position of a score among all ranked cells (0 = lowest, 1 = highest). Scores cluster in a narrow band, so colouring by
 * percentile (not min..max) is what makes the best pockets visibly stand out. */
function percentile(sorted: number[], v: number): number {
  let lo = 0, hi = sorted.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (sorted[mid] < v) lo = mid + 1; else hi = mid }
  return sorted.length > 1 ? lo / (sorted.length - 1) : 0.5
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

function FitCity({ cells, runId }: { cells: OppCell[]; runId: number }) {
  const map = useMap()
  useEffect(() => {
    const pts = cells.filter((c) => c.o !== null).flatMap((c) => c.p.map(([lon, lat]) => [lat, lon] as [number, number]))
    const all = pts.length ? pts : cells.flatMap((c) => c.p.map(([lon, lat]) => [lat, lon] as [number, number]))
    if (all.length) map.fitBounds(L.latLngBounds(all), { padding: [12, 12] })
    // fit once per run, not on every selection
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, map])
  return null
}

/** One canvas-rendered GeoJSON layer for the whole city (about 5,000 squares). It is created once per run and restyled in
 * place when the colour mode or selection changes, never remounted, so selecting a cell stays instant. */
export default function OpportunityMap({ runId, cells, mode, selectedId, top, stores, onSelect }: Props) {
  const layer = useRef<L.GeoJSON | null>(null)
  const [zoom, setZoom] = useState(11)
  const sortedScores = useMemo(() => cells.filter((c) => c.o !== null).map((c) => c.o as number).sort((a, b) => a - b), [cells])
  const topIds = useMemo(() => new Set(top.map((t) => t.cell_id)), [top])
  const data = useMemo(
    () => ({
      type: 'FeatureCollection',
      features: cells.map((c) => ({
        type: 'Feature',
        properties: { id: c.id, o: c.o, c: c.c },
        geometry: { type: 'Polygon', coordinates: [[...c.p, c.p[0]]] },
      })),
    }) as GeoJSON.FeatureCollection,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [runId],
  )

  const style = useMemo(
    () => (f?: GeoJSON.Feature): L.PathOptions => {
      const p = f?.properties as { id: string; o: number | null; c: number }
      const isSel = p.id === selectedId
      const isTop = topIds.has(p.id)
      let fill: string
      let opacity = 0.78
      if (p.o === null) {
        fill = OPP.unranked
        opacity = 0.22
      } else if (mode === 'coverage') {
        fill = OPP.rampCoverage(p.c)
      } else {
        fill = OPP.ramp(percentile(sortedScores, p.o))
      }
      return {
        stroke: isSel || isTop,
        color: isSel ? OPP.selected : OPP.top,
        weight: isSel ? 4 : 3,
        fillColor: fill,
        fillOpacity: opacity,
      }
    },
    [mode, sortedScores, selectedId, topIds],
  )

  useEffect(() => {
    const l = layer.current
    if (!l) return
    l.setStyle(style as L.StyleFunction)
    l.eachLayer((sub) => {
      const id = ((sub as L.Polygon).feature?.properties as { id?: string } | undefined)?.id
      if (id && (id === selectedId || topIds.has(id))) (sub as L.Path).bringToFront()
    })
  }, [style, selectedId, topIds])

  return (
    <MapContainer preferCanvas center={[13.0827, 80.2707]} zoom={11} minZoom={9} maxZoom={19} className="opp-map-inner"
      scrollWheelZoom>
      <TileLayer maxZoom={19}
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <GeoJSON key={runId} ref={layer} data={data} style={style as L.StyleFunction}
        onEachFeature={(f, l) => l.on('click', () => onSelect((f.properties as { id: string }).id))} />
      {stores.map((s) => (
        <CircleMarker key={`${s.name}${s.lat}`} center={[s.lat, s.lon]} radius={5}
          pathOptions={{ color: '#FFF200', weight: 2, fillColor: MAP.store, fillOpacity: 1 }}>
          <Tooltip>Savomart · {s.name}</Tooltip>
        </CircleMarker>
      ))}
      {top.map((t) => (
        <Marker key={t.cell_id} position={[t.lat, t.lon]} icon={rankIcon(t.rank, zoom)} zIndexOffset={(top.length + 1 - t.rank) * 100}
          eventHandlers={{ click: () => onSelect(t.cell_id) }}>
          <Tooltip>#{t.rank} {t.locality} {t.opportunity_score?.toFixed(1)}</Tooltip>
        </Marker>
      ))}
      <ZoomWatcher onZoom={setZoom} />
      <FitCity cells={cells} runId={runId} />
      <AutoResize />
    </MapContainer>
  )
}
