import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { Assignment, Duplicate, FieldError, PropertyDetail, PropertyForm } from '../../types'
import { Choice, Field, inr, NumberInput, num, YesNo } from '../common/ui'
import { compressImage } from './imageUtils'
import LocationStep, { type Loc } from './LocationStep'

const STEPS = ['Location', 'Rent & lease', 'Building', 'Access & parking', 'Competitors seen', 'Photos', 'Review & submit']
const FIELD_STEP: Record<string, number> = {
  location: 0, address: 0, locality: 0, pincode: 0,
  monthly_rent: 1, security_deposit: 1, lease_duration_months: 1, rent_negotiable: 1,
  property_type: 2, total_area_sqft: 2, ground_floor_area_sqft: 2, sales_area_sqft: 2, storage_area_sqft: 2,
  frontage_ft: 2, road_width_ft: 2, floor: 2, number_of_floors: 2,
  is_main_road_frontage: 3, is_corner_property: 3, traffic_signal_nearby: 3, signal_distance_m: 3, entry_access: 3,
  exit_access: 3, visibility_score: 3, two_wheeler_parking: 3, four_wheeler_parking: 3, parking_capacity: 3, parking_type: 3,
}
const stepOf = (field: string) => (field.startsWith('photo:') ? 5 : FIELD_STEP[field] ?? 6)
const PHOTO_LABEL: Record<string, string> = {
  front_view: 'Front of the shop', road_view: 'Road in front', interior_view: 'Inside', side_view: 'Side view',
  parking_view: 'Parking', building_condition: 'Building condition',
}
const FIELDS = Object.keys(FIELD_STEP).filter((f) => f !== 'location')
const KINDS: [string, string][] = [['supermarket', 'Supermarket'], ['organised_grocery', 'Organised grocery'], ['convenience', 'Convenience'], ['kirana', 'Kirana'], ['other', 'Other']]

type Form = Partial<PropertyForm>

function toBody(form: Form, only?: string[]) {
  const out: Record<string, unknown> = {}
  for (const k of only ?? FIELDS) {
    const v = (form as Record<string, unknown>)[k]
    if (v !== undefined) out[k] = v
  }
  return out
}

export default function PropertyWizard({ assignmentId, propertyId, onDone }: {
  assignmentId: number | null; propertyId: number | null; onDone: (id: number) => void
}) {
  const [step, setStep] = useState(0)
  const [form, setForm] = useState<Form>({})
  const [loc, setLoc] = useState<Loc | null>(null)
  const [locDirty, setLocDirty] = useState(false)
  const [pid, setPid] = useState<number | null>(propertyId)
  const [prop, setProp] = useState<PropertyDetail | null>(null)
  const [assignment, setAssignment] = useState<Assignment | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [warnings, setWarnings] = useState<FieldError[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  const [ack, setAck] = useState<{ needs: string; message: string; dups: Duplicate[] } | null>(null)
  const [loading, setLoading] = useState(!!propertyId)
  const draftKey = `draft:${assignmentId ?? 'none'}`

  // ---- load existing draft, assignment, or a locally mirrored draft
  useEffect(() => {
    if (assignmentId) api.assignment(assignmentId).then(setAssignment).catch(() => undefined)
    if (propertyId) {
      api.property(propertyId).then((p) => {
        applyProp(p)
        setLoading(false)
        if (p.assignment_id && !assignmentId) api.assignment(p.assignment_id).then(setAssignment).catch(() => undefined)
      }).catch((e: ApiError) => { setBanner(e.message); setLoading(false) })
    } else {
      try {
        const raw = localStorage.getItem(draftKey)
        if (raw) {
          const d = JSON.parse(raw)
          if (d.loc || Object.keys(d.form ?? {}).length) {
            setForm(d.form ?? {})
            setLoc(d.loc ?? null)
            setBanner('Restored your unsaved draft from this device.')
          }
        }
      } catch { /* storage unavailable */ }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- mirror unsaved input locally so a weak-network drop loses nothing
  useEffect(() => {
    if (pid) return
    try { localStorage.setItem(draftKey, JSON.stringify({ form, loc })) } catch { /* ignore */ }
  }, [form, loc, pid, draftKey])

  function applyProp(p: PropertyDetail) {
    setProp(p)
    setPid(p.property_id)
    const f: Form = {}
    for (const k of FIELDS) {
      const v = (p as unknown as Record<string, unknown>)[k]
      if (v !== null && v !== undefined) (f as Record<string, unknown>)[k] = v
    }
    setForm((prev) => ({ ...f, ...(propertyId ? {} : prev) }))
    setLoc((prev) => prev ?? { lat: p.lat, lon: p.lon, accuracy: p.location_accuracy_m, source: p.location_source as Loc['source'] })
  }

  const set = useCallback(<K extends keyof PropertyForm>(k: K, v: PropertyForm[K] | null) => {
    setForm((f) => ({ ...f, [k]: v === null ? null : v } as Form))
    setErrors((e) => { if (!e[k]) return e; const n = { ...e }; delete n[k]; return n })
  }, [])

  const mapErrors = (list: FieldError[]) => {
    const m: Record<string, string> = {}
    list.forEach((e) => { if (!m[e.field]) m[e.field] = e.message })
    setErrors(m)
    return m
  }

  // ---- persist to the server (creates the draft on first save)
  async function save(): Promise<boolean> {
    setBusy('save')
    setBanner(null)
    try {
      let p: PropertyDetail
      if (!pid) {
        if (!loc) { setErrors({ location: 'Set the property location first: use your current location or tap the map.' }); setStep(0); return false }
        p = await api.createProperty({
          lat: loc.lat, lon: loc.lon, location_accuracy_m: loc.accuracy, location_source: loc.source,
          assignment_id: assignmentId ?? undefined, acknowledge_duplicates: true, ...toBody(form),
        })
        try { localStorage.removeItem(draftKey) } catch { /* ignore */ }
        window.history.replaceState(null, '', `#/property/${p.property_id}/edit`)
      } else {
        const body: Record<string, unknown> = toBody(form)
        if (loc && locDirty) Object.assign(body, { lat: loc.lat, lon: loc.lon, location_accuracy_m: loc.accuracy, location_source: loc.source })
        p = await api.patchProperty(pid, body)
      }
      setLocDirty(false)
      setWarnings(p.warnings ?? [])
      setProp(p)
      setPid(p.property_id)
      return true
    } catch (e) {
      const err = e as ApiError
      if (err.fieldErrors.length) {
        const m = mapErrors(err.fieldErrors)
        setStep(Math.min(...Object.keys(m).map(stepOf)))
      } else setBanner(err.message)
      return false
    } finally {
      setBusy(null)
    }
  }

  const refresh = async () => {
    if (!pid) return
    try {
      const p = await api.property(pid)
      setProp(p)
      // drop "photo required" errors for photo types that are now present
      const have = new Set(p.photos.map((x) => x.photo_type))
      setErrors((e) => Object.fromEntries(Object.entries(e).filter(([k]) => !(k.startsWith('photo:') && have.has(k.slice(6))))))
    } catch { /* keep the last known state */ }
  }
  useEffect(() => { setBanner((b) => (b && /need fixing/.test(b) ? null : b)) }, [step])

  const next = async () => {
    if (step === 0 && !loc) { setErrors({ location: 'Set the property location first: use your current location or tap the map.' }); return }
    if (!(await save())) return
    setStep((s) => Math.min(s + 1, STEPS.length - 1))
    window.scrollTo?.({ top: 0 })
  }
  const saveDraft = async () => { if (await save()) setBanner('Draft saved. You can leave and come back later.') }

  async function submit(acknowledge_duplicates = false, acknowledge_warnings = false) {
    if (!pid) return
    if (!(await save())) return
    setBusy('submit')
    setBanner(null)
    setAck(null)
    setErrors({})
    try {
      await api.submit(pid, { acknowledge_duplicates, acknowledge_warnings })
      onDone(pid)
    } catch (e) {
      const err = e as ApiError
      if (err.status === 422 && err.fieldErrors.length) {
        const m = mapErrors(err.fieldErrors)
        setBanner(`${Object.keys(m).length} thing(s) need fixing before this can be submitted.`)
      } else if (err.status === 409 && err.needs) {
        const d = err.detail as { duplicates?: Duplicate[]; message: string; warnings?: FieldError[] }
        setWarnings(d.warnings ?? [])
        setAck({ needs: err.needs, message: d.message, dups: d.duplicates ?? [] })
      } else setBanner(err.message)
    } finally {
      setBusy(null)
    }
  }

  const errorList = useMemo(() => Object.entries(errors), [errors])
  if (loading) return <div className="scroll-view"><div className="empty"><span className="loader" /></div></div>
  const F = form

  return (
    <div className="wizard">
      <div className="wiz-head">
        <div className="wiz-title">
          <span>Step {step + 1} of {STEPS.length}</span>
          <b>{STEPS[step]}</b>
        </div>
        <div className="wiz-bar" role="progressbar" aria-valuemin={1} aria-valuemax={STEPS.length} aria-valuenow={step + 1}>
          <i style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} />
        </div>
        {assignment && <div className="hint">Assignment: <b>{assignment.hotspot_label ?? assignment.area_name}</b> · {assignment.area_name}</div>}
      </div>

      <div className="wiz-body stack">
        {banner && <div className="msg info" role="status">{banner}</div>}

        {step === 0 && (
          <LocationStep
            loc={loc}
            setLoc={(l) => { setLoc(l); setLocDirty(true); setErrors((e) => { const n = { ...e }; delete n.location; return n }) }}
            assignment={assignment}
            propertyId={pid}
            address={F.address} locality={F.locality} pincode={F.pincode}
            onPrefill={(v) => setForm((f) => ({ ...f, ...v }))}
            errors={errors}
          />
        )}

        {step === 1 && (
          <>
            <Field label="Monthly rent (₹)" required error={errors.monthly_rent}>
              <NumberInput value={F.monthly_rent} onChange={(v) => set('monthly_rent', v as number)} placeholder="e.g. 85000" />
            </Field>
            <Field label="Is the rent negotiable?" required error={errors.rent_negotiable}>
              <YesNo value={F.rent_negotiable} onChange={(v) => set('rent_negotiable', v)} />
            </Field>
            <Field label="Security deposit (₹)" error={errors.security_deposit}>
              <NumberInput value={F.security_deposit} onChange={(v) => set('security_deposit', v as number)} />
            </Field>
            <Field label="Lease duration (months)" error={errors.lease_duration_months}>
              <NumberInput value={F.lease_duration_months} onChange={(v) => set('lease_duration_months', v as number)} step="1" />
            </Field>
          </>
        )}

        {step === 2 && (
          <>
            <Field label="Property type" required error={errors.property_type}>
              <select className="input" value={F.property_type ?? ''} onChange={(e) => set('property_type', e.target.value)}>
                <option value="">Choose…</option>
                <option value="shop">Shop</option><option value="showroom">Showroom</option>
                <option value="standalone">Standalone building</option><option value="mall_unit">Mall unit</option><option value="other">Other</option>
              </select>
            </Field>
            <div className="two-col">
              <Field label="Total usable area" required error={errors.total_area_sqft}>
                <NumberInput value={F.total_area_sqft} onChange={(v) => set('total_area_sqft', v as number)} suffix="sq ft" />
              </Field>
              <Field label="Ground-floor area" required error={errors.ground_floor_area_sqft}>
                <NumberInput value={F.ground_floor_area_sqft} onChange={(v) => set('ground_floor_area_sqft', v as number)} suffix="sq ft" />
              </Field>
              <Field label="Sales area" hint="Only if measured" error={errors.sales_area_sqft}>
                <NumberInput value={F.sales_area_sqft} onChange={(v) => set('sales_area_sqft', v as number)} suffix="sq ft" />
              </Field>
              <Field label="Storage area" hint="Only if measured" error={errors.storage_area_sqft}>
                <NumberInput value={F.storage_area_sqft} onChange={(v) => set('storage_area_sqft', v as number)} suffix="sq ft" />
              </Field>
              <Field label="Frontage" required error={errors.frontage_ft}>
                <NumberInput value={F.frontage_ft} onChange={(v) => set('frontage_ft', v as number)} suffix="ft" />
              </Field>
              <Field label="Road width" error={errors.road_width_ft}>
                <NumberInput value={F.road_width_ft} onChange={(v) => set('road_width_ft', v as number)} suffix="ft" />
              </Field>
              <Field label="Floor (0 = ground)" required error={errors.floor}>
                <NumberInput value={F.floor} onChange={(v) => set('floor', v as number)} step="1" />
              </Field>
              <Field label="Floors in building" required error={errors.number_of_floors}>
                <NumberInput value={F.number_of_floors} onChange={(v) => set('number_of_floors', v as number)} step="1" min={1} />
              </Field>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <Field label="On the main road?" required error={errors.is_main_road_frontage}>
              <YesNo value={F.is_main_road_frontage} onChange={(v) => set('is_main_road_frontage', v)} />
            </Field>
            <Field label="Corner property?" required error={errors.is_corner_property}>
              <YesNo value={F.is_corner_property} onChange={(v) => set('is_corner_property', v)} />
            </Field>
            <Field label="Traffic signal nearby?" error={errors.traffic_signal_nearby} hint="Busy roads bring footfall but can make entry and exit hard. Rate entry and exit honestly.">
              <YesNo value={F.traffic_signal_nearby} onChange={(v) => set('traffic_signal_nearby', v)} />
            </Field>
            {F.traffic_signal_nearby && (
              <Field label="Distance to signal" error={errors.signal_distance_m}>
                <NumberInput value={F.signal_distance_m} onChange={(v) => set('signal_distance_m', v as number)} suffix="m" />
              </Field>
            )}
            <Field label="Entry to the shop" required error={errors.entry_access}>
              <Choice value={F.entry_access} onChange={(v) => set('entry_access', v)} options={[['easy', 'Easy'], ['moderate', 'Moderate'], ['difficult', 'Difficult']]} />
            </Field>
            <Field label="Exit from the shop" required error={errors.exit_access}>
              <Choice value={F.exit_access} onChange={(v) => set('exit_access', v)} options={[['easy', 'Easy'], ['moderate', 'Moderate'], ['difficult', 'Difficult']]} />
            </Field>
            <Field label="Visibility from the road (1 = hidden, 5 = excellent)" error={errors.visibility_score}>
              <Choice value={F.visibility_score ? String(F.visibility_score) : null} onChange={(v) => set('visibility_score', Number(v))} options={[['1', '1'], ['2', '2'], ['3', '3'], ['4', '4'], ['5', '5']]} />
            </Field>
            <Field label="Two-wheeler parking?" required error={errors.two_wheeler_parking}>
              <YesNo value={F.two_wheeler_parking} onChange={(v) => set('two_wheeler_parking', v)} />
            </Field>
            <Field label="Four-wheeler parking?" required error={errors.four_wheeler_parking}>
              <YesNo value={F.four_wheeler_parking} onChange={(v) => set('four_wheeler_parking', v)} />
            </Field>
            <div className="two-col">
              <Field label="Parking capacity" hint="Vehicles" error={errors.parking_capacity}>
                <NumberInput value={F.parking_capacity} onChange={(v) => set('parking_capacity', v as number)} step="1" />
              </Field>
              <Field label="Parking type" error={errors.parking_type}>
                <select className="input" value={F.parking_type ?? ''} onChange={(e) => set('parking_type', e.target.value || null)}>
                  <option value="">Not sure</option><option value="on_property">On the property</option>
                  <option value="roadside">Roadside</option><option value="both">Both</option><option value="none">None</option>
                </select>
              </Field>
            </div>
          </>
        )}

        {step === 4 && <CompetitorStep pid={pid} prop={prop} refresh={refresh} />}
        {step === 5 && <PhotoStep pid={pid} prop={prop} refresh={refresh} errors={errors} required={prop?.required_photo_types ?? ['front_view', 'road_view', 'interior_view']} />}

        {step === 6 && (
          <>
            <ReviewSummary form={F} loc={loc} prop={prop} />
            {errorList.length > 0 && (
              <div className="msg err" role="alert">
                <b>Fix these before submitting:</b>
                <ul className="err-list">
                  {errorList.map(([f, m]) => (
                    <li key={f}><button type="button" className="linklike" onClick={() => setStep(stepOf(f))}>{m}</button></li>
                  ))}
                </ul>
              </div>
            )}
            {warnings.length > 0 && !ack && (
              <div className="msg warn"><b>Check:</b>{warnings.map((w, i) => <div key={i}>{w.message}</div>)}</div>
            )}
            {ack && (
              <div className="msg warn" role="alert">
                <b>{ack.message}</b>
                {ack.dups.map((d) => (
                  <div key={d.property_id}>
                    {d.message} <a href={`#/property/${d.property_id}`} target="_blank" rel="noreferrer">Open #{d.property_id}</a>
                  </div>
                ))}
                {warnings.map((w, i) => <div key={i}>{w.message}</div>)}
                <button className="btn yellow block" style={{ marginTop: 10 }} disabled={busy !== null}
                  onClick={() => submit(true, true)}>
                  {ack.needs === 'duplicate_acknowledgement' ? 'It is a different property: submit anyway' : 'Confirm and submit'}
                </button>
              </div>
            )}
            <button className="btn yellow block" disabled={busy !== null || !pid} onClick={() => submit(false, false)}>
              {busy === 'submit' ? <span className="loader" /> : null} Submit for manager review
            </button>
            <p className="hint">The system will evaluate the property automatically once it is submitted.</p>
          </>
        )}
      </div>

      <div className="wiz-foot">
        <button className="btn ghost" disabled={step === 0 || busy !== null} onClick={() => setStep((s) => s - 1)}>Back</button>
        <button className="btn ghost" disabled={busy !== null || (!pid && !loc)} onClick={saveDraft}>
          {busy === 'save' ? <span className="loader" /> : null} Save draft
        </button>
        {step < STEPS.length - 1 && <button className="btn" disabled={busy !== null} onClick={next}>Next</button>}
      </div>
    </div>
  )
}

function CompetitorStep({ pid, prop, refresh }: { pid: number | null; prop: PropertyDetail | null; refresh: () => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState('supermarket')
  const [dist, setDist] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (!pid) return <div className="msg warn">Save the location first (tap Back, then Next).</div>
  const add = async () => {
    if (!name.trim()) { setErr('Enter the competitor name.'); return }
    try {
      await api.addCompetitor(pid, { name, kind, approx_distance_m: dist })
      setName(''); setDist(null); setErr(null); refresh()
    } catch (e) { setErr((e as ApiError).message) }
  }
  return (
    <>
      <p className="hint">List grocery stores you saw near the property. Public data is used too; these are your own observations. Skip if none.</p>
      {(prop?.field_competitors ?? []).map((c) => (
        <div className="row-card" key={c.id}>
          <div><b>{c.name}</b><div className="hint">{KINDS.find((k) => k[0] === c.kind)?.[1]}{c.approx_distance_m != null ? ` · about ${c.approx_distance_m} m away` : ''}</div></div>
          <button className="btn ghost sm" onClick={async () => { await api.deleteCompetitor(pid, c.id); refresh() }}>Remove</button>
        </div>
      ))}
      <Field label="Name" error={err ?? undefined}>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Reliance Smart" />
      </Field>
      <Field label="Type"><Choice value={kind} onChange={setKind} options={KINDS.slice(0, 3)} /></Field>
      <div className="seg" style={{ gridTemplateColumns: 'repeat(2,1fr)', marginTop: -4 }}>
        {KINDS.slice(3).map(([v, l]) => <button key={v} type="button" aria-pressed={kind === v} onClick={() => setKind(v)}>{l}</button>)}
      </div>
      <Field label="Approx. distance (m)"><NumberInput value={dist} onChange={setDist} suffix="m" /></Field>
      <button className="btn block" onClick={add}>Add competitor</button>
    </>
  )
}

function PhotoStep({ pid, prop, refresh, errors, required }: {
  pid: number | null; prop: PropertyDetail | null; refresh: () => void; errors: Record<string, string>; required: string[]
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  if (!pid) return <div className="msg warn">Save the location first (tap Back, then Next).</div>
  const types = prop?.photo_types ?? ['front_view', 'road_view', 'interior_view', 'side_view', 'parking_view', 'building_condition']
  const pick = async (type: string, file: File | undefined) => {
    if (!file) return
    setBusy(type); setMsg(null)
    try {
      const blob = await compressImage(file)
      await api.uploadPhoto(pid, type, blob)
      refresh()
    } catch (e) {
      setMsg(`${(e as Error).message} Your other photos are safe. Try this one again.`)
    } finally { setBusy(null) }
  }
  return (
    <>
      <p className="hint">Take clear photos from the street. Required ones are marked *. Photos are compressed for weak networks.</p>
      {msg && <div className="msg err" role="alert">{msg}</div>}
      {types.map((t) => {
        const mine = (prop?.photos ?? []).filter((p) => p.photo_type === t)
        const isReq = required.includes(t)
        const err = errors[`photo:${t}`]
        return (
          <div className={`photo-slot${err ? ' has-error' : ''}`} key={t}>
            <div className="ps-head">
              <b>{PHOTO_LABEL[t] ?? t}{isReq ? ' *' : ''}</b>
              <label className="btn sm ghost">
                {busy === t ? <span className="loader" /> : mine.length ? '+ Add another' : '📷 Take / choose'}
                <input type="file" accept="image/*" capture="environment" hidden disabled={busy !== null}
                  onChange={(e) => { pick(t, e.target.files?.[0]); e.target.value = '' }} />
              </label>
            </div>
            {mine.length > 0 && (
              <div className="thumbs">
                {mine.map((p) => (
                  <div className="thumb" key={p.photo_id}>
                    <img src={p.url} alt={PHOTO_LABEL[t]} />
                    <button type="button" aria-label="Remove photo" onClick={async () => { await api.deletePhoto(pid, p.photo_id); refresh() }}>×</button>
                  </div>
                ))}
              </div>
            )}
            {err && <p className="field-error" role="alert">{err}</p>}
          </div>
        )
      })}
    </>
  )
}

function ReviewSummary({ form, loc, prop }: { form: Form; loc: Loc | null; prop: PropertyDetail | null }) {
  const rows: [string, string][] = [
    ['Address', [form.address, form.locality, form.pincode].filter(Boolean).join(', ') || '-'],
    ['Pin', loc ? `${loc.lat.toFixed(5)}, ${loc.lon.toFixed(5)}${loc.accuracy != null ? ` (±${Math.round(loc.accuracy)} m)` : ' (placed on map)'}` : '-'],
    ['Rent', `${inr(form.monthly_rent)} / month${form.rent_negotiable ? ' (negotiable)' : ''}`],
    ['Area', `${num(form.total_area_sqft, ' sq ft')} total · ${num(form.ground_floor_area_sqft, ' sq ft')} ground`],
    ['Frontage / road', `${num(form.frontage_ft, ' ft')} / ${num(form.road_width_ft, ' ft')}`],
    ['Access', `${form.is_main_road_frontage ? 'Main road' : 'Not main road'} · ${form.is_corner_property ? 'corner' : 'not corner'} · entry ${form.entry_access ?? '-'} · exit ${form.exit_access ?? '-'}`],
    ['Parking', `2W ${form.two_wheeler_parking == null ? '-' : form.two_wheeler_parking ? 'yes' : 'no'} · 4W ${form.four_wheeler_parking == null ? '-' : form.four_wheeler_parking ? 'yes' : 'no'}`],
    ['Photos', `${prop?.photos.length ?? 0} uploaded`],
    ['Competitors seen', `${prop?.field_competitors.length ?? 0}`],
  ]
  return (
    <section className="card">
      <h2>Review</h2>
      <dl className="review-list">{rows.map(([k, v]) => (<div key={k}><dt>{k}</dt><dd>{v}</dd></div>))}</dl>
    </section>
  )
}
