import { useEffect, useState } from 'react'
import { api, getPersona, setPersona } from './api/client'
import AreaPanel from './components/AreaSelector/AreaPanel'
import { AssignmentDetail, AssignmentList } from './components/Executive/ExecutivePages'
import PropertyList from './components/Manager/PropertyList'
import PropertyWizard from './components/PropertyCapture/PropertyWizard'
import PropertyReview from './components/PropertyReview/PropertyReview'
import { StudyDetail, StudyList } from './components/Survey/SurveyManagerPages'
import { UnitDetailPage, UnitList } from './components/Survey/SurveyExecutivePages'
import ReportList from './components/ReportHistory/ReportList'
import ReportPage from './components/ReportView/ReportPage'
import type { Persona } from './types'

type Route =
  | { name: 'analyse' } | { name: 'reports' } | { name: 'report'; id: number }
  | { name: 'properties' } | { name: 'property'; id: number } | { name: 'edit'; id: number }
  | { name: 'new'; assignment: number | null }
  | { name: 'assignments' } | { name: 'assignment'; id: number }
  | { name: 'studies' } | { name: 'study'; id: number } | { name: 'units' } | { name: 'unit'; id: number }

function parse(hash: string): Route {
  let m = hash.match(/^#\/report\/(\d+)/)
  if (m) return { name: 'report', id: Number(m[1]) }
  m = hash.match(/^#\/property\/(\d+)\/edit/)
  if (m) return { name: 'edit', id: Number(m[1]) }
  m = hash.match(/^#\/property\/(\d+)/)
  if (m) return { name: 'property', id: Number(m[1]) }
  m = hash.match(/^#\/new(?:\/(\d+))?/)
  if (m) return { name: 'new', assignment: m[1] ? Number(m[1]) : null }
  m = hash.match(/^#\/assignment\/(\d+)/)
  if (m) return { name: 'assignment', id: Number(m[1]) }
  m = hash.match(/^#\/study\/(\d+)/)
  if (m) return { name: 'study', id: Number(m[1]) }
  m = hash.match(/^#\/unit\/(\d+)/)
  if (m) return { name: 'unit', id: Number(m[1]) }
  if (hash.startsWith('#/studies')) return { name: 'studies' }
  if (hash.startsWith('#/units')) return { name: 'units' }
  if (hash.startsWith('#/assignments')) return { name: 'assignments' }
  if (hash.startsWith('#/properties')) return { name: 'properties' }
  if (hash.startsWith('#/reports')) return { name: 'reports' }
  return { name: 'home' } as unknown as Route
}

const FALLBACK: Persona[] = [
  { id: 'bd_manager:asha', name: 'Asha (BD Manager)', role: 'bd_manager', role_label: 'BD Manager', active: true, job: '' },
  { id: 'bd_executive:ravi', name: 'Ravi (BD Executive)', role: 'bd_executive', role_label: 'BD Executive', active: true, job: '' },
  { id: 'bd_executive:divya', name: 'Divya (BD Executive)', role: 'bd_executive', role_label: 'BD Executive', active: true, job: '' },
  { id: 'survey_manager:meena', name: 'Meena (Survey Manager)', role: 'survey_manager', role_label: 'Survey Manager', active: true, job: '' },
  { id: 'survey_executive:karthik', name: 'Karthik (Survey Executive)', role: 'survey_executive', role_label: 'Survey Executive', active: true, job: '' },
  { id: 'survey_executive:lakshmi', name: 'Lakshmi (Survey Executive)', role: 'survey_executive', role_label: 'Survey Executive', active: true, job: '' },
]

const Icon = {
  map: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z" /><path d="M9 4v14M15 6v14" /></svg>,
  doc: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h5" /></svg>,
  building: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 21V5l8-2v18M12 9l8 2v10M8 9h1M8 13h1M8 17h1M16 15h1M16 18h1" /></svg>,
  pin: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 21s7-6.2 7-11a7 7 0 0 0-14 0c0 4.8 7 11 7 11z" /><circle cx="12" cy="10" r="2.5" /></svg>,
}

export default function App() {
  const [hash, setHash] = useState(window.location.hash)
  const [personas, setPersonas] = useState<Persona[]>(FALLBACK)
  const [personaId, setPersonaId] = useState<string>(getPersona())

  useEffect(() => {
    const on = () => setHash(window.location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  useEffect(() => { api.personas().then(setPersonas).catch(() => undefined) }, [])

  const persona = personas.find((p) => p.id === personaId) ?? personas[0]
  const isMgr = persona.role === 'bd_manager'
  const isExec = persona.role === 'bd_executive'
  const isSM = persona.role === 'survey_manager'
  const isSE = persona.role === 'survey_executive'
  const go = (h: string) => { window.location.hash = h }
  const choose = (id: string) => {
    setPersona(id)
    setPersonaId(id)
    const p = personas.find((x) => x.id === id)
    go(p?.role === 'bd_executive' ? '#/assignments' : p?.role === 'survey_manager' ? '#/studies' : p?.role === 'survey_executive' ? '#/units' : '#/')
  }

  let r = parse(hash)
  // role-shaped home screen
  if ((r as { name: string }).name === 'home') r = isExec ? { name: 'assignments' } : isSM ? { name: 'studies' } : isSE ? { name: 'units' } : { name: 'analyse' }
  const tab = r.name === 'analyse' ? 'analyse' : r.name === 'reports' || r.name === 'report' ? 'reports'
    : r.name === 'properties' || r.name === 'property' || r.name === 'edit' || r.name === 'new' ? 'properties'
    : r.name === 'studies' || r.name === 'study' ? 'studies' : r.name === 'units' || r.name === 'unit' ? 'units' : 'assignments'

  let body
  if (!persona.active) {
    body = (
      <div className="scroll-view"><div className="page"><div className="empty card">
        <h2>{persona.role_label} view</h2><p>{persona.job}</p>
        <p className="hint" style={{ marginTop: 8 }}>This role's screens arrive in Milestone 3. Switch to the BD Manager or a BD Executive.</p>
        <button className="btn yellow" style={{ marginTop: 16 }} onClick={() => choose('bd_manager:asha')}>Switch to BD Manager</button>
      </div></div></div>
    )
  } else if (isSM) {
    body = r.name === 'study' ? <StudyDetail key={r.id} id={r.id} onBack={() => go('#/studies')} />
      : r.name === 'unit' ? <UnitDetailPage id={r.id} onBack={() => go('#/studies')} /> : <StudyList onOpen={(id) => go(`#/study/${id}`)} />
  } else if (isSE) {
    body = r.name === 'unit' ? <UnitDetailPage key={r.id} id={r.id} onBack={() => go('#/units')} /> : <UnitList onOpen={(id) => go(`#/unit/${id}`)} />
  } else if (isExec) {
    if (r.name === 'assignment') body = <AssignmentDetail id={r.id} onBack={() => go('#/assignments')} onAdd={() => go(`#/new/${r.id}`)} onOpenProperty={(id, st) => go(st === 'ASSIGNED' ? `#/property/${id}/edit` : `#/property/${id}`)} />
    else if (r.name === 'new') body = <PropertyWizard key={`new-${r.assignment}`} assignmentId={r.assignment} propertyId={null} onDone={(id) => go(`#/property/${id}`)} />
    else if (r.name === 'edit') body = <PropertyWizard key={`edit-${r.id}`} assignmentId={null} propertyId={r.id} onDone={(id) => go(`#/property/${id}`)} />
    else if (r.name === 'property') body = <PropertyReview id={r.id} onBack={() => go('#/properties')} onEdit={(id) => go(`#/property/${id}/edit`)} />
    else if (r.name === 'properties') body = <PropertyList mine onOpen={(id) => go(`#/property/${id}`)} />
    else body = <AssignmentList onOpen={(id) => go(`#/assignment/${id}`)} />
  } else {
    if (r.name === 'report') body = <ReportPage reportId={r.id} onBack={() => go('#/reports')} />
    else if (r.name === 'reports') body = <ReportList onOpen={(id) => go(`#/report/${id}`)} />
    else if (r.name === 'properties') body = <PropertyList onOpen={(id) => go(`#/property/${id}`)} />
    else if (r.name === 'property') body = <PropertyReview id={r.id} onBack={() => go('#/properties')} onEdit={(id) => go(`#/property/${id}/edit`)} />
    else body = <AreaPanel onStarted={(id) => go(`#/report/${id}`)} />
  }

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            <svg viewBox="0 0 32 32"><path d="M16 5c-4.4 0-8 3.4-8 7.6C8 18.4 16 27 16 27s8-8.6 8-14.4C24 8.4 20.4 5 16 5z" fill="#782B90" /><circle cx="16" cy="12.6" r="3.2" fill="#FFF200" /></svg>
          </span>
          Savo SiteScout <small>Chennai expansion intelligence</small>
        </div>
        {persona.active && (
          <nav className="bottom-nav" aria-label="Main">
            {isMgr && <>
              <button aria-current={tab === 'analyse' ? 'page' : undefined} onClick={() => go('#/')}>{Icon.map} Analyse</button>
              <button aria-current={tab === 'reports' ? 'page' : undefined} onClick={() => go('#/reports')}>{Icon.doc} Reports</button>
              <button aria-current={tab === 'properties' ? 'page' : undefined} onClick={() => go('#/properties')}>{Icon.building} Properties</button>
            </>}
            {isSM && <button aria-current={tab === 'studies' ? 'page' : undefined} onClick={() => go('#/studies')}>{Icon.map} Studies</button>}
            {isSE && <button aria-current={tab === 'units' ? 'page' : undefined} onClick={() => go('#/units')}>{Icon.pin} My units</button>}
            {isExec && <>
              <button aria-current={tab === 'assignments' ? 'page' : undefined} onClick={() => go('#/assignments')}>{Icon.pin} Assignments</button>
              <button aria-current={tab === 'properties' ? 'page' : undefined} onClick={() => go('#/properties')}>{Icon.building} My properties</button>
            </>}
          </nav>
        )}
        <div className="spacer" />
        <label htmlFor="persona" style={{ position: 'absolute', left: -9999 }}>Switch role</label>
        <select id="persona" className="persona-select" value={persona.id} onChange={(e) => choose(e.target.value)}>
          {personas.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </header>
      {body}
    </div>
  )
}
