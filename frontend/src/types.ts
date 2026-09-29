export type Persona = { id: string; name: string; role: string; active: boolean; job: string }

export type Area = {
  area_id: number
  name: string
  input_type: 'pincode' | 'name' | 'grid_cells'
  raw_input: string
  geometry: GeoJSON.Polygon
  area_km2: number
  boundary_quality: 'exact' | 'approximate'
  note?: string
}

export type Factor = {
  key: string
  label: string
  group: string
  weight: number
  raw: number | null
  unit: string
  norm: number
  points: number
  explanation: string
}

export type Step = {
  node: string
  label: string
  status: 'pending' | 'running' | 'done' | 'failed'
  started_at: string | null
  finished_at: string | null
  note: string
}

export type ReportSummary = {
  report_id: number
  area_id: number
  area_name: string
  input_type: string
  area_km2: number
  status: 'queued' | 'running' | 'completed' | 'failed'
  created_at: string
  completed_at: string | null
  overall_score: number | null
  rating: string | null
  data_quality_flags: string[]
  explanation_source: string | null
}

export type ReportStatus = {
  report_id: number
  status: ReportSummary['status']
  steps: Step[]
  failed_node: string | null
  error: string | null
}

export type Hotspot = {
  rank: number
  cell_id: string
  score: number
  lat: number
  lon: number
  locality: string
  nearest_named_road: string | null
  why: string[]
}

export type Explanation = {
  summary: string
  reasons: { factor: string; text: string }[]
  scout_first: { cell_id: string; text: string }[]
  caveats: string[]
}

export type DataSource = { source: string; url: string; mocked: boolean; fetched_at: string | null; note?: string }

export type ReportDetail = ReportSummary &
  ReportStatus & {
    score_breakdown: Factor[]
    explanation: Explanation | null
    data_sources: DataSource[]
    area_geometry: GeoJSON.Polygon
    profile: Record<string, number | string | boolean | string[] | null>
    hotspots: Hotspot[]
  }

export type Store = { store_code: string; name: string; latitude: number; longitude: number }

export type Compare = {
  reports: (ReportSummary & { profile: ReportDetail['profile']; hotspots: Hotspot[] })[]
  factors: ({ key: string; label: string; weight: number } & Record<string, unknown>)[]
  best_report_id: number
}
