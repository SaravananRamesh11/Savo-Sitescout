import type { Hotspot } from '../../types'

export default function HotspotList({ hotspots }: { hotspots: Hotspot[] }) {
  return (
    <section className="card">
      <h2>Scout here first</h2>
      {hotspots.length === 0 && <p className="hint">No hotspots were identified for this area.</p>}
      {hotspots.map((h) => (
        <article className="hotspot" key={h.cell_id}>
          <div className="rank" aria-label={`Rank ${h.rank}`}>{h.rank}</div>
          <div>
            <h3>
              {h.locality} <span className="score">{h.score.toFixed(1)}</span>
            </h3>
            <p className="meta">
              {h.nearest_named_road ? `Near ${h.nearest_named_road} · ` : ''}
              <a
                href={`https://www.openstreetmap.org/?mlat=${h.lat}&mlon=${h.lon}#map=17/${h.lat}/${h.lon}`}
                target="_blank"
                rel="noreferrer"
              >
                {h.lat.toFixed(5)}, {h.lon.toFixed(5)}
              </a>{' '}
              · cell {h.cell_id}
            </p>
            {h.why.length > 0 && (
              <ul>
                {h.why.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
          </div>
        </article>
      ))}
    </section>
  )
}
