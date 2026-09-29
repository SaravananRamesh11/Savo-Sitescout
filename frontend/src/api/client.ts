import type {
  Area, Assignment, Capture, CaptureType, Compare, Duplicate, Evaluation, FieldError, Insights, Persona, PropertyDetail,
  PropertySummary, ReportDetail, ReportStatus, ReportSummary, SplitPreview, Stage, Store, Study, StudyRow, UnitDetail, WorkUnit,
} from '../types'

/** `detail` carries structured server payloads (field errors, duplicate/warning acknowledgements). */
export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(message: string, status = 0, detail: unknown = null) {
    super(message)
    this.status = status
    this.detail = detail
  }
  get fieldErrors(): FieldError[] {
    const d = this.detail as { errors?: FieldError[] } | null
    return d && Array.isArray(d.errors) ? d.errors : []
  }
  get needs(): string | null {
    const d = this.detail as { needs?: string } | null
    return d && typeof d.needs === 'string' ? d.needs : null
  }
}

// The role switcher picks who is acting; the API checks it (seeded personas, not real auth).
let personaId = 'bd_manager:asha'
try {
  const saved = localStorage.getItem('persona')
  if (saved && saved.includes(':')) personaId = saved
} catch {
  /* storage unavailable */
}
export const setPersona = (id: string) => {
  personaId = id
  try {
    localStorage.setItem('persona', id)
  } catch {
    /* ignore */
  }
}
export const getPersona = () => personaId

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = init.body instanceof FormData
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), 'X-Persona': personaId, ...(init.headers ?? {}) },
    })
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection and try again.')
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`
    let detail: unknown = null
    try {
      const body = await res.json()
      detail = body.detail
      if (typeof body.detail === 'string') msg = body.detail
      else if (body.detail?.message) msg = body.detail.message
      else if (Array.isArray(body.detail?.errors) && body.detail.errors[0]) msg = body.detail.errors[0].message
    } catch {
      /* keep default */
    }
    throw new ApiError(msg, res.status, detail)
  }
  return res.json() as Promise<T>
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  personas: () => req<Persona[]>('/personas'),
  executives: () => req<{ id: string; name: string }[]>('/executives'),
  pincodes: () => req<{ pincode: string; label: string }[]>('/pincodes'),
  resolve: (type: 'pincode' | 'name' | 'grid_cells', value: string | string[]) => req<Area>('/areas/resolve', json('POST', { type, value })),
  grid: (bbox: string) => req<GeoJSON.FeatureCollection>(`/grid?bbox=${bbox}`),
  stores: () => req<{ stores: Store[]; meta: { mocked: boolean; source: string } }>('/stores'),
  generate: (areaId: number) => req<{ report_id: number }>('/reports/generate', json('POST', { area_id: areaId })),
  retry: (id: number) => req<{ report_id: number }>(`/reports/${id}/retry`, json('POST')),
  status: (id: number) => req<ReportStatus>(`/reports/${id}/status`),
  report: (id: number) => req<ReportDetail>(`/reports/${id}?include_cells=false`),
  reports: () => req<ReportSummary[]>('/reports'),
  compare: (ids: number[]) => req<Compare>(`/reports/compare?ids=${ids.join(',')}`),

  // M2
  createAssignment: (body: Record<string, unknown>) => req<Assignment>('/assignments', json('POST', body)),
  assignments: () => req<Assignment[]>('/assignments'),
  assignment: (id: number) => req<Assignment>(`/assignments/${id}`),
  properties: (stage?: string) => req<PropertySummary[]>(`/properties${stage ? `?stage=${stage}` : ''}`),
  property: (id: number) => req<PropertyDetail>(`/properties/${id}`),
  createProperty: (body: Record<string, unknown>) => req<PropertyDetail>('/properties', json('POST', body)),
  patchProperty: (id: number, body: Record<string, unknown>) => req<PropertyDetail>(`/properties/${id}`, json('PATCH', body)),
  duplicateCheck: (body: Record<string, unknown>) => req<{ duplicates: Duplicate[] }>('/properties/duplicate-check', json('POST', body)),
  reverse: (lat: number, lon: number) =>
    req<{ address: string | null; locality: string | null; pincode: string | null }>(`/geo/reverse?lat=${lat}&lon=${lon}`),
  uploadPhoto: (id: number, type: string, blob: Blob) => {
    const f = new FormData()
    f.append('photo_type', type)
    f.append('file', blob, `${type}.jpg`)
    return req<{ photo_id: number; url: string }>(`/properties/${id}/photos`, { method: 'POST', body: f })
  },
  deletePhoto: (id: number, photoId: number) => req(`/properties/${id}/photos/${photoId}`, json('DELETE')),
  addCompetitor: (id: number, body: Record<string, unknown>) => req(`/properties/${id}/competitors`, json('POST', body)),
  deleteCompetitor: (id: number, cid: number) => req(`/properties/${id}/competitors/${cid}`, json('DELETE')),
  submit: (id: number, body: { acknowledge_duplicates?: boolean; acknowledge_warnings?: boolean }) =>
    req<PropertyDetail>(`/properties/${id}/submit`, json('POST', body)),
  evaluationStatus: (id: number) => req<{ latest: Evaluation | null; has_completed: boolean }>(`/properties/${id}/evaluation-status`),
  transition: (id: number, to_stage: Stage, reason?: string, notes?: string, force_new?: boolean) =>
    req<PropertyDetail>(`/properties/${id}/transition`, json('POST', { to_stage, reason, notes, force_new })),
  reEvaluate: (id: number) => req<{ version: number }>(`/properties/${id}/re-evaluate`, json('POST')),

  // M3: ground catchment survey
  requestStudy: (body: { property_id?: number; area_id?: number; force_new?: boolean }) =>
    req<Study & { outcome: string }>('/catchments', json('POST', body)),
  studies: () => req<StudyRow[]>('/catchments'),
  study: (id: number) => req<Study>(`/catchments/${id}`),
  areaCatchment: (areaId: number) => req<StudyRow | null>(`/areas/${areaId}/catchment`),
  splitPreview: (id: number, units?: number) => req<SplitPreview>(`/catchments/${id}/split-preview`, json('POST', { units })),
  createUnits: (id: number, units: number | undefined, assignments: string[]) =>
    req<{ units: WorkUnit[] }>(`/catchments/${id}/work-units`, json('POST', { units, assignments })),
  insightsPreview: (id: number) => req<Insights>(`/catchments/${id}/insights-preview`),
  completeStudy: (id: number) => req<Study>(`/catchments/${id}/complete`, json('POST')),
  regenerateInsights: (id: number) => req<Insights>(`/catchments/${id}/insights`, json('POST')),
  insightVersions: (id: number) => req<Insights[]>(`/catchments/${id}/insights`),
  surveyUnits: () => req<(WorkUnit & { study_id: number; label: string; locality: string | null; kind: string })[]>('/survey/units'),
  surveyUnit: (id: number) => req<UnitDetail>(`/survey/units/${id}`),
  startUnit: (id: number) => req<UnitDetail>(`/survey/units/${id}/start`, json('POST')),
  completeUnit: (id: number) => req<UnitDetail>(`/survey/units/${id}/complete`, json('POST')),
  addCapture: (id: number, body: { capture_type: CaptureType; data: Record<string, unknown>; lat: number; lon: number; accuracy?: number | null }) =>
    req<Capture & { unit: WorkUnit }>(`/survey/units/${id}/captures`, json('POST', body)),
  editCapture: (id: number, body: Record<string, unknown>) => req<Capture>(`/survey/captures/${id}`, json('PATCH', body)),
  deleteCapture: (id: number) => req(`/survey/captures/${id}`, json('DELETE')),
  addCapturePhoto: (id: number, type: string, blob: Blob, loc?: { lat: number; lon: number; accuracy?: number | null }) => {
    const f = new FormData()
    f.append('photo_type', type)
    f.append('file', blob, `${type}.jpg`)
    if (loc) {
      f.append('lat', String(loc.lat))
      f.append('lon', String(loc.lon))
      if (loc.accuracy != null) f.append('accuracy', String(loc.accuracy))
    }
    return req<{ photo_id: number; url: string }>(`/survey/captures/${id}/photos`, { method: 'POST', body: f })
  },
  deleteCapturePhoto: (id: number, pid: number) => req(`/survey/captures/${id}/photos/${pid}`, json('DELETE')),
}
