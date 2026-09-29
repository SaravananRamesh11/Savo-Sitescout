import type { Area, Compare, Persona, ReportDetail, ReportStatus, ReportSummary, Store } from '../types'

export class ApiError extends Error {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection and try again.')
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') msg = body.detail
    } catch {
      /* keep default */
    }
    throw new ApiError(msg)
  }
  return res.json() as Promise<T>
}

export const api = {
  personas: () => req<Persona[]>('/personas'),
  pincodes: () => req<{ pincode: string; label: string }[]>('/pincodes'),
  resolve: (type: 'pincode' | 'name' | 'grid_cells', value: string | string[]) =>
    req<Area>('/areas/resolve', { method: 'POST', body: JSON.stringify({ type, value }) }),
  grid: (bbox: string) => req<GeoJSON.FeatureCollection>(`/grid?bbox=${bbox}`),
  stores: () => req<{ stores: Store[]; meta: { mocked: boolean; source: string } }>('/stores'),
  generate: (areaId: number) =>
    req<{ report_id: number }>('/reports/generate', { method: 'POST', body: JSON.stringify({ area_id: areaId }) }),
  retry: (id: number) => req<{ report_id: number }>(`/reports/${id}/retry`, { method: 'POST' }),
  status: (id: number) => req<ReportStatus>(`/reports/${id}/status`),
  report: (id: number) => req<ReportDetail>(`/reports/${id}?include_cells=false`),
  reports: () => req<ReportSummary[]>('/reports'),
  compare: (ids: number[]) => req<Compare>(`/reports/compare?ids=${ids.join(',')}`),
}
