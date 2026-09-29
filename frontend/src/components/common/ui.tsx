import type { ReactNode } from 'react'
import type { Stage } from '../../types'

export const STAGE_LABEL: Record<Stage, string> = {
  ASSIGNED: 'In the field (draft)',
  SUBMITTED: 'Submitted',
  UNDER_REVIEW: 'Under review',
  CATCHMENT_REQUESTED: 'Catchment requested',
  CATCHMENT_IN_PROGRESS: 'Catchment in progress',
  CATCHMENT_COMPLETED: 'Catchment completed',
  FINAL_REVIEW: 'Final review',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
}

export const REC_LABEL: Record<string, string> = {
  PROCEED_TO_CATCHMENT: 'Proceed to catchment study',
  REVIEW: 'Needs manager review',
  NOT_RECOMMENDED: 'Not recommended',
}

export function StageChip({ stage, sentBack }: { stage: Stage; sentBack?: boolean }) {
  const tone = stage === 'REJECTED' ? 'warn' : stage === 'APPROVED' || stage === 'FINAL_REVIEW' ? 'yellow' : ''
  // A property the manager sent back returns to ASSIGNED: show it as "sent back", not as an in-field draft.
  const label = stage === 'ASSIGNED' && sentBack ? 'Sent back to executive' : STAGE_LABEL[stage] ?? stage
  return <span className={`chip ${tone}`}>{label}</span>
}

export function Field({ label, error, hint, children, required }: {
  label: string; error?: string; hint?: string; children: ReactNode; required?: boolean
}) {
  return (
    <div className={`field${error ? ' has-error' : ''}`}>
      <label className="field-label">{label}{required ? <span aria-hidden> *</span> : null}</label>
      {children}
      {hint && !error && <p className="hint" style={{ marginTop: 4 }}>{hint}</p>}
      {error && <p className="field-error" role="alert">{error}</p>}
    </div>
  )
}

export function NumberInput({ value, onChange, placeholder, step, min, suffix }: {
  value: number | null | undefined; onChange: (v: number | null) => void; placeholder?: string; step?: string; min?: number; suffix?: string
}) {
  return (
    <div className="input-wrap">
      <input
        className="input"
        type="number"
        inputMode="decimal"
        min={min ?? 0}
        step={step ?? 'any'}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      />
      {suffix && <span className="suffix">{suffix}</span>}
    </div>
  )
}

export function YesNo({ value, onChange, labels = ['Yes', 'No'] }: {
  value: boolean | null | undefined; onChange: (v: boolean) => void; labels?: [string, string]
}) {
  return (
    <div className="seg two" role="group">
      <button type="button" aria-pressed={value === true} onClick={() => onChange(true)}>{labels[0]}</button>
      <button type="button" aria-pressed={value === false} onClick={() => onChange(false)}>{labels[1]}</button>
    </div>
  )
}

export function Choice({ value, options, onChange }: {
  value: string | null | undefined; options: [string, string][]; onChange: (v: string) => void
}) {
  return (
    <div className="seg" style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)` }} role="group">
      {options.map(([v, label]) => (
        <button type="button" key={v} aria-pressed={value === v} onClick={() => onChange(v)}>{label}</button>
      ))}
    </div>
  )
}

export const inr = (v: number | null | undefined) => (v == null ? '-' : `₹${Math.round(v).toLocaleString('en-IN')}`)
export const num = (v: number | null | undefined, unit = '') => (v == null ? '-' : `${v.toLocaleString('en-IN')}${unit}`)
export const pct = (v: number | null | undefined, d = 0) => (v == null ? '-' : `${(v * 100).toFixed(d)}%`)

export function Fact({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'warn' | 'good' }) {
  return (
    <div className={`fact${tone ? ' ' + tone : ''}`}>
      <b>{value}</b>
      <span>{label}</span>
      {sub && <em>{sub}</em>}
    </div>
  )
}
