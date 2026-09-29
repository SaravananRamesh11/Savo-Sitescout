export const fmtTime = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '-'

export const fmtNum = (v: unknown) => (typeof v === 'number' ? v.toLocaleString('en-IN') : String(v ?? '-'))

export const FLAG_TEXT: Record<string, string> = {
  population_mocked: 'Population, household and growth figures are estimates, not official Census data.',
  osm_mocked: 'OpenStreetMap was unreachable: amenity, road and competition figures are synthetic placeholders.',
  osm_stale_cache: 'OpenStreetMap was unreachable: an older cached copy was used.',
  savomart_mocked: 'Savomart stores API unavailable: a saved snapshot of stores was used.',
  savomart_stale: 'Savomart stores API unavailable: previously saved stores were used.',
  area_boundary_approximate: 'The area boundary is approximate (centroid or Voronoi based).',
  llm_output_rejected: 'The AI wording was rejected by the number check, so a fixed template was used.',
}
