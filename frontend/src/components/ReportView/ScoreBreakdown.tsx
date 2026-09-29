import { useState } from 'react'
import type { Factor } from '../../types'

// One series, one hue: bar length = points earned out of the factor's weight. Tap a row for the raw number.
export default function ScoreBreakdown({ factors }: { factors: Factor[] }) {
  const [open, setOpen] = useState<string | null>(null)
  const groups = [...new Set(factors.map((f) => f.group))]
  return (
    <section className="card">
      <h2>How the score was built</h2>
      <p className="hint" style={{ marginBottom: 12 }}>
        Every number comes from data, not from AI. Bars show points earned out of the maximum for each signal. Tap a row to see the raw value.
      </p>
      {groups.map((g) => {
        const rows = factors.filter((f) => f.group === g)
        const pts = rows.reduce((s, f) => s + f.points, 0)
        const max = rows.reduce((s, f) => s + f.weight, 0)
        return (
          <div className="bd-group" key={g}>
            <div className="bd-group-head">
              <span>{g}</span>
              <span>{pts.toFixed(1)} / {max}</span>
            </div>
            {rows.map((f) => (
              <button
                className="bd-row"
                key={f.key}
                onClick={() => setOpen(open === f.key ? null : f.key)}
                aria-expanded={open === f.key}
              >
                <div className="bd-top">
                  <span>{f.label}</span>
                  <b>{f.points.toFixed(1)} / {f.weight}</b>
                </div>
                <div className="bd-track">
                  <div className="bd-fill" style={{ width: `${(f.points / f.weight) * 100}%` }} />
                </div>
                {open === f.key && <div className="bd-explain">{f.explanation}</div>}
              </button>
            ))}
          </div>
        )
      })}
    </section>
  )
}
