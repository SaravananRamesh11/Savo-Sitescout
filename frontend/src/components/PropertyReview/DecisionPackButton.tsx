import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'

/** Decision Pack: a leadership-ready PDF of the whole case (M1, M2, M3, decision), generated on demand for an approved property. */
export default function DecisionPackButton({ propertyId }: { propertyId: number }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pack, setPack] = useState<{ url: string; file: File } | null>(null)

  useEffect(() => () => { if (pack) URL.revokeObjectURL(pack.url) }, [pack])

  const generate = async () => {
    setBusy(true)
    setError(null)
    try {
      const blob = await api.decisionPack(propertyId)
      const file = new File([blob], `decision-pack-${propertyId}.pdf`, { type: 'application/pdf' })
      setPack({ url: URL.createObjectURL(blob), file })
    } catch (e) {
      setError((e as ApiError).message || 'Could not generate the Decision Pack.')
    } finally {
      setBusy(false)
    }
  }
  const canShare = !!pack && typeof navigator.canShare === 'function' && navigator.canShare({ files: [pack.file] })
  const share = () => {
    if (pack) navigator.share({ files: [pack.file], title: `Decision Pack, property #${propertyId}` }).catch(() => undefined)
  }

  return (
    <div className="stack" aria-label="Decision Pack">
      {!pack ? (
        <button className="btn yellow block" onClick={generate} disabled={busy}>
          {busy ? 'Preparing the PDF…' : 'Generate Decision Pack'}
        </button>
      ) : (
        <>
          <div className="msg info" role="status">The Decision Pack is ready. It covers the property, the area analysis, the evaluation, the ground survey and the decision.</div>
          <a className="btn block" href={pack.url} target="_blank" rel="noreferrer">View PDF</a>
          <a className="btn ghost block" href={pack.url} download={pack.file.name}>Download PDF</a>
          {canShare && <button className="btn ghost block" onClick={share}>Share</button>}
          <button className="btn ghost block" onClick={() => setPack(null)}>Generate again</button>
        </>
      )}
      {busy && <p className="hint">Reading the photos and survey; this takes a few seconds.</p>}
      {error && <div className="msg err">{error}</div>}
    </div>
  )
}
