import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { ChatResponse, ChatSource, ChatTurn } from '../../types'

const SUGGESTIONS = [
  'Compare Velachery and Tambaram',
  'Show properties in Velachery',
  'Which property has the highest M2 score?',
  'Which areas have completed catchment studies?',
  'What areas are in Chennai that I can scout?',
  'How many supermarkets are near Adyar?',
]
const SOURCE_LABEL: Record<ChatSource['type'], string> = { area_report: 'Area report', property: 'Property', catchment: 'Catchment study', external: 'Source' }

const SearchIcon = (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
)

type Msg = { role: 'user' | 'assistant'; content: string; sources?: ChatSource[]; mode?: ChatResponse['mode'] }

/** The model is told to answer in plain text; this only tidies stray markdown and turns "- " lines into a list. */
function AnswerText({ text }: { text: string }) {
  const lines = text.replace(/\*\*/g, '').replace(/^#+\s*/gm, '').split('\n').map((l) => l.trimEnd())
  const out: React.ReactNode[] = []
  let list: string[] = []
  const flush = () => { if (list.length) { out.push(<ul key={`u${out.length}`}>{list.map((x, i) => <li key={i}>{x}</li>)}</ul>); list = [] } }
  lines.forEach((l) => {
    const m = l.match(/^\s*[-*•]\s+(.*)/)
    if (m) list.push(m[1])
    else { flush(); if (l.trim()) out.push(<p key={`p${out.length}`}>{l}</p>) }
  })
  flush()
  return <>{out}</>
}

export default function Assistant({ onOpen }: { onOpen: (href: string) => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [msgs, busy])

  const send = (q: string) => {
    const question = q.trim()
    if (!question || busy) return
    const history: ChatTurn[] = msgs.slice(-6).map((m) => ({ role: m.role, content: m.content }))
    setMsgs((m) => [...m, { role: 'user', content: question }])
    setText('')
    setError(null)
    setBusy(true)
    api.analystChat(question, history)
      .then((r) => setMsgs((m) => [...m, { role: 'assistant', content: r.answer, sources: r.sources, mode: r.mode }]))
      .catch((e: ApiError) => setError(e.message || 'Something went wrong. Please try again.'))
      .finally(() => setBusy(false))
  }

  const started = msgs.length > 0 || busy
  const bar = (
    <form className={`ask-bar${started ? ' docked' : ''}`} role="search" onSubmit={(e) => { e.preventDefault(); send(text) }}>
      <label htmlFor="chat-q" className="sr-only">Type your question</label>
      <span className="ask-icon" aria-hidden>{SearchIcon}</span>
      <input id="chat-q" type="search" value={text} maxLength={500} autoComplete="off" enterKeyHint="send"
        placeholder={started ? 'Ask a follow-up…' : 'Type your own question…'} onChange={(e) => setText(e.target.value)} />
      <button className="ask-go" type="submit" disabled={busy || !text.trim()} aria-label="Ask">{busy ? <span className="loader" /> : 'Ask'}</button>
    </form>
  )

  return (
    <>
      <div className="scroll-view chat-scroll">
        <div className="page stack">
          <div className="chat-head">
            <div>
              <h1 style={{ fontSize: 22 }}>Ask the analyst</h1>
              <p className="hint">Ask anything about your areas, properties and ground surveys. Answers come only from your saved data.</p>
            </div>
            {started && <button className="btn ghost sm" onClick={() => { setMsgs([]); setError(null); setText('') }} disabled={busy}>New chat</button>}
          </div>
          {!started && (
            <>
              {bar}
              <div className="suggest-box">
                <p className="hint">Or try one of these:</p>
                <div className="suggest" role="group" aria-label="Example questions">
                  {SUGGESTIONS.map((s) => <button key={s} className="pill" onClick={() => send(s)}>{s}</button>)}
                </div>
              </div>
            </>
          )}
          <div className="chat-list" aria-live="polite">
            {msgs.map((m, i) => (
              <div key={i} className={`bubble ${m.role}${m.mode === 'busy' || m.mode === 'unavailable' ? ' soft' : ''}`}>
                {m.role === 'user' ? m.content : <AnswerText text={m.content} />}
                {!!m.sources?.length && (
                  <div className="src-row" aria-label="Sources">
                    {m.sources.map((s) => (
                      <button key={`${s.type}${s.id}`} className="src-chip" disabled={!s.href} onClick={() => { if (!s.href) return; if (s.href.startsWith('http')) window.open(s.href, '_blank', 'noopener,noreferrer'); else onOpen(s.href) }}>
                        <b>{SOURCE_LABEL[s.type]}</b> {s.label.length > 38 ? s.label.slice(0, 36) + '…' : s.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {busy && <div className="bubble assistant"><span className="loader" /> <span className="hint">Looking at your data…</span></div>}
          </div>
          {error && <div className="msg err">{error}</div>}
          <div ref={end} />
        </div>
      </div>
      {started && <div className="chat-foot">{bar}</div>}
    </>
  )
}
