export type Persona = { id: string; name: string; role: string; role_label: string; active: boolean; job: string }

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

// ------------------------------------------------------------------ M2
export type Stage =
  | 'ASSIGNED' | 'SUBMITTED' | 'UNDER_REVIEW' | 'CATCHMENT_REQUESTED' | 'CATCHMENT_IN_PROGRESS'
  | 'CATCHMENT_COMPLETED' | 'FINAL_REVIEW' | 'APPROVED' | 'REJECTED'

export type Assignment = {
  assignment_id: number; area_id: number; area_name: string; area_geometry: GeoJSON.Polygon
  source_report_id: number | null; hotspot_cell_id: string | null; hotspot_lat: number | null; hotspot_lon: number | null
  hotspot_label: string | null; hotspot_locality?: string | null; hotspot_road?: string | null; executive_id: string; executive_name: string; assigned_by: string
  notes: string | null; due_date: string | null; status: 'OPEN' | 'DONE' | 'CANCELLED'; created_at: string
  properties_captured: number
}

export type PropertyForm = {
  address: string; locality: string; pincode: string
  total_area_sqft: number; ground_floor_area_sqft: number; sales_area_sqft: number; storage_area_sqft: number
  frontage_ft: number; road_width_ft: number; floor: number; number_of_floors: number; property_type: string
  monthly_rent: number; security_deposit: number; lease_duration_months: number; rent_negotiable: boolean
  expected_monthly_revenue: number; revenue_source: string
  is_corner_property: boolean; is_main_road_frontage: boolean; traffic_signal_nearby: boolean; signal_distance_m: number
  entry_access: string; exit_access: string; visibility_score: number
  two_wheeler_parking: boolean; four_wheeler_parking: boolean; parking_capacity: number; parking_type: string
}

export type Photo = { photo_id: number; photo_type: string; url: string; size_bytes: number }
export type FieldError = { field: string; message: string }
export type Duplicate = { property_id: number; distance_m: number; message: string; reason: string; address?: string | null; stage?: string }

export type EvalFactor = {
  key: string; label: string; weight: number; effective_weight: number; scored: boolean; norm: number | null
  points: number | null; raw: number | null; explanation: string; estimated_input: boolean
}
export type Risk = { code: string; severity: 'high' | 'medium' | 'low'; text: string }
export type Evaluation = {
  evaluation_id: number; version: number; status: 'running' | 'completed' | 'failed'; error: string | null
  overall_score: number | null; confidence: number | null; recommendation: string | null; trigger: string
  created_at: string; created_by: string; explanation_source: string | null
  score_breakdown?: EvalFactor[]; insights?: { factor: string; tone: string; text: string }[]; risks?: Risk[]
  explanation?: { summary: string; reasons: { factor: string; text: string }[]; risk_notes?: { code: string; text: string }[]; caveats: string[] }
  data_sources?: { source: string; mocked: boolean; fetched_at: string | null; note?: string }[]
  data_quality_flags?: string[]
  m1_context?: {
    area_report_id: number | null; area_score: number | null; cell_id: string | null; cell_score: number | null
    is_hotspot: boolean | null; hotspot_rank: number | null; nearest_savomart: string | null
    nearest_savomart_m: number | null; savomart_within_1km: number
  }
  metrics?: {
    rent_per_sqft: number | null; rent_to_revenue: number | null; sales_ratio: number | null
    storage_ratio: number | null; storage_to_sales: number | null
    poi?: { available: boolean; counts?: Record<string, Record<string, number>>; organised?: Record<string, number>
      other_grocery?: Record<string, number>; nearest_organised_m?: number | null }
    demographics?: { pop_density: number; household_density: number; growth_pct: number; mocked: boolean; nearest_locality: string }
    field_competitors?: { name: string; kind: string; approx_distance_m: number | null }[]
    recommendation_reason?: string
  }
}

export type PropertySummary = {
  property_id: number; area_id: number; area_name: string; address: string | null; locality: string | null
  lat: number; lon: number; pipeline_stage: Stage; monthly_rent: number | null; total_area_sqft: number | null
  rent_per_sqft: number | null; created_by: string; created_by_name: string; created_at: string
  submitted_at: string | null; photo_url: string | null; photo_count: number; possible_duplicate: boolean
  assignment_id: number | null; evaluation: Evaluation | null; evaluation_status: string | null
}

export type PropertyDetail = PropertySummary & Partial<PropertyForm> & {
  location_accuracy_m: number | null; location_source: string
  duplicate_flags: { property_id: number; distance_m: number; reason: string; acknowledged_by: string | null }[]
  photos: Photo[]; field_competitors: { id: number; name: string; kind: string; approx_distance_m: number | null; notes: string | null }[]
  previous_evaluation: Evaluation | null; evaluation_versions: Evaluation[]; latest_evaluation_status: Evaluation | null
  history: { from_stage: string | null; to_stage: string; changed_by_name: string; changed_at: string; reason: string | null; notes: string | null; evaluation_id?: number | null; evaluation_version?: number | null }[]
  original_evaluation?: Evaluation | null; updated_evaluation?: Evaluation | null; score_change?: number | null
  final_decision?: { decision: 'APPROVED' | 'REJECTED'; decided_by: string; decided_by_name: string; decided_at: string; reason: string | null; based_on_evaluation: Evaluation | null } | null
  catchment?: unknown | null
  allowed_next_stages: Stage[]; required_photo_types: string[]; photo_types: string[]; can_edit: boolean; storage_backend: string
  duplicates?: Duplicate[]; warnings?: FieldError[]
}
