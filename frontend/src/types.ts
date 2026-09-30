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
  catchment?: PropertyCatchment | null
  allowed_next_stages: Stage[]; required_photo_types: string[]; photo_types: string[]; can_edit: boolean; storage_backend: string
  duplicates?: Duplicate[]; warnings?: FieldError[]
}

// ------------------------------------------------------------------ M3: ground catchment survey
export type StudyStatus = 'REQUESTED' | 'IN_PROGRESS' | 'COMPLETED'
export type UnitStatus = 'ASSIGNED' | 'IN_PROGRESS' | 'COMPLETED'
export type CaptureType = 'residential' | 'commercial' | 'competition' | 'traffic' | 'accessibility' | 'demand_generator' | 'local_condition'

export type Insights = {
  insight_id?: number; version?: number; preview?: boolean; coverage_percentage: number
  residential: Record<string, any>; commercial: Record<string, any>; competition: Record<string, any>
  traffic: Record<string, any>; accessibility: Record<string, any>; demand_generators: Record<string, any>
  key_findings: string[]; risks: { code: string; severity: 'high' | 'medium' | 'low'; text: string }[]
  ground_fit_score: number | null; data_quality_flags: string[]; generated_at?: string; generated_by?: string
}
export type EvidencePhoto = { photo_id: number; url: string; photo_type: string; capture_type: string; lat: number | null; lon: number | null; captured_at: string }

export type StudyRow = {
  study_id: number; status: StudyStatus; label: string; sublabel: string | null; kind: 'property' | 'area'
  requested_by: string; requested_at: string; started_at: string | null; completed_at: string | null
  property_id: number | null; area_id: number | null; reused: boolean; reused_from_study_id: number | null
  reuse_reason: string | null; data_quality_flags: string[]; units_total?: number; units_completed?: number; needs_split?: boolean
}
export type WorkUnit = {
  unit_id: number; unit_code: string; status: UnitStatus; priority: number; assigned_to: string; assigned_to_name: string
  estimated_distance_m: number | null; target_capture_count: number | null; completed_capture_count: number
  workload: { points: number; lanes: { name: string; length_m: number }[]; road_m: number | null; commercial_pois: number | null
    amenity_pois: number | null; estimated_households: number; basis: string } | null
  started_at: string | null; completed_at: string | null; geometry?: GeoJSON.MultiPolygon
}
export type Study = StudyRow & {
  study_geometry: GeoJSON.Polygon; units?: WorkUnit[]; can_split?: boolean; can_complete?: boolean
  progress?: { units_total: number; units_completed: number; captures: number; target: number; coverage_percentage: number }
  insights?: Insights | null; evidence_photos?: EvidencePhoto[]; outcome?: string
}
export type SplitPreview = {
  study_id: number; study_geometry: GeoJSON.Polygon
  units: { index: number; unit_code: string; geometry: GeoJSON.MultiPolygon; estimated_distance_m: number | null
    target_capture_count: number; workload: NonNullable<WorkUnit['workload']>; suggested_assignee: string; suggested_assignee_name: string }[]
  meta: { unit_count: number; total_points: number; balance_ratio: number; flags: string[]; study_area_km2: number }
  executives: { id: string; name: string; open_points: number }[]
}
export type Capture = {
  capture_id: number; capture_type: CaptureType; data: Record<string, any>; lat: number; lon: number; accuracy_m: number | null
  captured_at: string; photos: { photo_id: number; photo_type: string; url: string }[]; warning?: string | null
}
export type UnitDetail = WorkUnit & {
  study_id: number; label: string; locality: string | null; kind: 'property' | 'area'; property_lat?: number | null
  property_lon?: number | null; captures: Capture[]; study_status: StudyStatus; geometry: GeoJSON.MultiPolygon; note?: string | null
}
export type PropertyCatchment = {
  study_id: number; status: StudyStatus; requested_at: string; started_at: string | null; completed_at: string | null
  reused: boolean; reused_from_study_id: number | null; reuse_reason: string | null; data_quality_flags: string[]
  insights?: Insights | null; evidence_photos?: EvidencePhoto[]; insight_versions?: number[]; survey_age_days?: number
}

export type ChatSource = { type: 'area_report' | 'property' | 'catchment' | 'external'; id: number; label: string; href: string | null }
export type ChatTurn = { role: 'user' | 'assistant'; content: string }
export type ChatResponse = { answer: string; sources: ChatSource[]; tools_used: { tool: string; args: Record<string, unknown> }[]; mode: 'llm' | 'template' | 'help' | 'busy' | 'unavailable' }

// ---- Opportunity Finder
export type OppCell = { id: string; p: number[][]; o: number | null; c: number; w: string | null }
export type OppFactor = { key: string; label: string; weight: number; points: number; raw: number | string | null; unit: string; norm: number; explanation: string }
export type OppStore = { name: string; lat: number; lon: number; distance_m: number; road_distance_m?: number; road_duration_s?: number }
export type OppSignal = { kind: string; ref: number; units: number; age_days: number; share: number }
export type OppDetail = {
  cell_id: string; ranked: boolean; rank?: number | null; lat: number; lon: number
  reason?: string; reason_text?: string
  locality?: string; opportunity_score?: number; m1_score?: number; rating?: string; w_unscouted?: number
  breakdown?: OppFactor[]; positives?: string[]; risks?: string[]; flags?: string[]
  features?: Record<string, unknown>; nearest_stores?: OppStore[]
  scouting?: { coverage: number; summary: string; signals: OppSignal[] }
  map_data?: { status: string | null; fetched_at: string | null }
}
export type OppStatus = {
  run_id: number; status: 'queued' | 'running' | 'completed' | 'failed'; error: string | null
  progress: { phase: string; done: number; total: number; message: string }; created_at: string; completed_at: string | null
}
export type OppSource = { source: string; url: string; mocked: boolean; fetched_at: string | null; oldest_tile_fetched_at?: string | null; note?: string }
export type OppRun = OppStatus & {
  config: { w_unscouted: number; coverage_full_units: number; recent_full_days: number; expire_days: number; top_n: number; [k: string]: unknown }
  summary: { cells_total: number; cells_ranked: number; unranked_by_reason: Record<string, number>; tiles_total: number; tiles_missing: number
    tiles_stale: number; tiles_live: number; tiles_from_cache: number; stores: { name: string; lat: number; lon: number }[]; score_range: [number, number] | null }
  top: (OppDetail & { rank: number })[]; data_quality_flags: string[]; data_sources: OppSource[]; reasons: Record<string, string>; cells: OppCell[]
}
