import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CircleMarker, GeoJSON, MapContainer, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import { api, ApiError } from '../../api/client'
import { MAP } from '../../theme/colors'
import type { Area, Store } from '../../types'

const CHENNAI: [number, number] = [13.0827, 80.2707]
const MIN_GRID_ZOOM = 14
export const MAX_CELLS = 100

export type CellFeature = GeoJSON.Feature<GeoJSON.Polygon, { cell_id: string }>

export function isContiguous(ids: string[]): boolean {
  if (ids.length === 0) return false
  const set = new Set(ids)
  const seen = new Set([ids[0]])
  const q = [ids[0]]
  while (q.length) {
    const [c, r] = q.pop()!.split('_').map(Number)
    for (const n of [`${c + 1}_${r}`, `${c - 1}_${r}`, `${c}_${r + 1}`, `${c}_${r - 1}`]) {
      if (set.has(n) && !seen.has(n)) {
        seen.add(n)
        q.push(n)
      }
    }
  }
  return seen.size === set.size
}

function AutoResize() {
  const map = useMap()
  useEffect(() => {
    const el = map.getContainer()
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(el)
    return () => ro.disconnect()
  }, [map])
  return null
}

function FitToArea({ area, bottomPad }: { area: Area | null; bottomPad: number }) {
  const map = useMap()
  useEffect(() => {
    if (!area) return
    const b = L.geoJSON(area.geometry).getBounds()
    map.fitBounds(b, { paddingTopLeft: [24, 24], paddingBottomRight: [24, bottomPad], maxZoom: 15 })
  }, [area, map, bottomPad])
  return null
}

function GridLayer({
  active,
  selected,
  cellCache,
  onToggle,
  onZoomState,
}: {
  active: boolean
  selected: Set<string>
  cellCache: React.MutableRefObject<Map<string, CellFeature>>
  onToggle: (id: string) => void
  onZoomState: (tooFar: boolean) => void
}) {
  const map = useMap()
  const [visible, setVisible] = useState<CellFeature[]>([])
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const load = useCallback(() => {
    if (!active) return
    if (map.getZoom() < MIN_GRID_ZOOM) {
      onZoomState(true)
      setVisible([])
      return
    }
    onZoomState(false)
    const b = map.getBounds()
    const bbox = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`
    api
      .grid(bbox)
      .then((fc) => {
        setError(null)
        const feats = fc.features as CellFeature[]
        feats.forEach((f) => cellCache.current.set(f.properties.cell_id, f))
        setVisible(feats)
      })
      .catch((e: ApiError) => setError(e.message))
  }, [active, map, cellCache, onZoomState])

  useEffect(() => {
    load()
  }, [load])
  useMapEvents({
    moveend: () => {
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(load, 250)
    },
  })

  const feats = useMemo(() => {
    if (!active) return []
    const map2 = new Map(visible.map((f) => [f.properties.cell_id, f]))
    selected.forEach((id) => {
      const f = cellCache.current.get(id)
      if (f) map2.set(id, f)
    })
    return [...map2.values()]
  }, [active, visible, selected, cellCache])

  if (!active) return null
  return (
    <>
      {error && <div className="zoom-note">{error}</div>}
      <GeoJSON
        key={`${feats.length}-${[...selected].join(',')}`}
        data={{ type: 'FeatureCollection', features: feats } as GeoJSON.FeatureCollection}
        style={(f) => {
          const on = selected.has(f?.properties?.cell_id)
          return {
            color: on ? MAP.boundary : MAP.cell,
            weight: on ? 2 : 1,
            fillColor: on ? MAP.selected : MAP.cell,
            fillOpacity: on ? 0.55 : 0.04,
          }
        }}
        onEachFeature={(f, layer) => {
          layer.on('click', () => onToggle(f.properties.cell_id))
        }}
      />
    </>
  )
}

export default function MapView({
  area,
  gridMode,
  selected,
  onToggleCell,
  bottomPad,
  onGridZoomState,
}: {
  area: Area | null
  gridMode: boolean
  selected: Set<string>
  onToggleCell: (id: string) => void
  bottomPad: number
  onGridZoomState: (tooFar: boolean) => void
}) {
  const [stores, setStores] = useState<Store[]>([])
  const cellCache = useRef<Map<string, CellFeature>>(new Map())
  useEffect(() => {
    api
      .stores()
      .then((r) => setStores(r.stores))
      .catch(() => setStores([]))
  }, [])

  return (
    <MapContainer center={CHENNAI} zoom={11} minZoom={9} className="map-wrap" zoomControl={false}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {area && (
        <GeoJSON
          key={area.area_id}
          data={area.geometry}
          style={{ color: MAP.boundary, weight: 3, fillColor: MAP.boundaryFill, fillOpacity: 0.12 }}
        />
      )}
      {stores
        .filter((s) => s.latitude > 12.6 && s.latitude < 13.5 && s.longitude > 79.8 && s.longitude < 80.6)
        .map((s) => (
          <CircleMarker
            key={s.store_code}
            center={[s.latitude, s.longitude]}
            radius={8}
            pathOptions={{ color: MAP.store, weight: 3, fillColor: '#FFF200', fillOpacity: 1 }}
          >
            <Tooltip>Savomart · {s.name}</Tooltip>
          </CircleMarker>
        ))}
      <GridLayer active={gridMode} selected={selected} cellCache={cellCache} onToggle={onToggleCell} onZoomState={onGridZoomState} />
      <AutoResize />
      <FitToArea area={area} bottomPad={bottomPad} />
    </MapContainer>
  )
}
