import { useEffect, useState } from 'react'
import { api } from './api/client'
import AreaPanel from './components/AreaSelector/AreaPanel'
import ReportList from './components/ReportHistory/ReportList'
import ReportPage from './components/ReportView/ReportPage'
import type { Persona } from './types'

type Route = { name: 'analyse' } | { name: 'reports' } | { name: 'report'; id: number }

function parse(hash: string): Route {
  const m = hash.match(/^#\/report\/(\d+)/)
  if (m) return { name: 'report', id: Number(m[1]) }
  if (hash.startsWith('#/reports')) return { name: 'reports' }
  return { name: 'analyse' }
}

const FALLBACK_PERSONAS: Persona[] = [
  { id: 'bd_manager', name: 'Asha (BD Manager)', role: 'BD Manager', active: true, job: '' },
  { id: 'bd_executive', name: 'Ravi (BD Executive)', role: 'BD Executive', active: false, job: 'Scout properties in the field (Milestone 2)' },
  { id: 'survey_manager', name: 'Meena (Survey Manager)', role: 'Survey Manager', active: false, job: 'Plan and assign catchment studies (Milestone 3)' },
  { id: 'survey_executive', name: 'Karthik (Survey Executive)', role: 'Survey Executive', active: false, job: 'Capture lane-level data (Milestone 3)' },
]

const Icon = {
  map: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z" /><path d="M9 4v14M15 6v14" /></svg>
  ),
  doc: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h5" /></svg>
  ),
}

export default function App() {
  const [route, setRoute] = useState<Route>(parse(window.location.hash))
  const [personas, setPersonas] = useState<Persona[]>(FALLBACK_PERSONAS)
  const [personaId, setPersonaId] = useState<string>(() => {
    try {
      return localStorage.getItem('persona') || 'bd_manager'
    } catch {
      return 'bd_manager'
    }
  })

  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  useEffect(() => {
    api.personas().then(setPersonas).catch(() => undefined)
  }, [])

  const go = (h: string) => {
    window.location.hash = h
  }
  const persona = personas.find((p) => p.id === personaId) ?? personas[0]
  const choose = (id: string) => {
    setPersonaId(id)
    try {
      localStorage.setItem('persona', id)
    } catch {
      /* storage unavailable: fine */
    }
  }
  const tab = route.name === 'analyse' ? 'analyse' : 'reports'

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
            <button aria-current={tab === 'analyse' ? 'page' : undefined} onClick={() => go('#/')}>{Icon.map} Analyse</button>
            <button aria-current={tab === 'reports' ? 'page' : undefined} onClick={() => go('#/reports')}>{Icon.doc} Reports</button>
          </nav>
        )}
        <div className="spacer" />
        <label className="sr-only" htmlFor="persona" style={{ position: 'absolute', left: -9999 }}>Switch role</label>
        <select id="persona" className="persona-select" value={persona.id} onChange={(e) => choose(e.target.value)}>
          {personas.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </header>

      {!persona.active ? (
        <div className="scroll-view">
          <div className="page">
            <div className="empty card">
              <h2>{persona.role} view</h2>
              <p>{persona.job}</p>
              <p className="hint" style={{ marginTop: 8 }}>This role's screens arrive in a later milestone. Switch to the BD Manager to use Area Intelligence.</p>
              <button className="btn yellow" style={{ marginTop: 16 }} onClick={() => choose('bd_manager')}>Switch to BD Manager</button>
            </div>
          </div>
        </div>
      ) : route.name === 'analyse' ? (
        <AreaPanel onStarted={(id) => go(`#/report/${id}`)} />
      ) : route.name === 'reports' ? (
        <ReportList onOpen={(id) => go(`#/report/${id}`)} />
      ) : (
        <ReportPage reportId={route.id} onBack={() => go('#/reports')} />
      )}
    </div>
  )
}
