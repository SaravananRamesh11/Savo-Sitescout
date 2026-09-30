// Savomart brand identity (mandatory). CSS custom properties in index.css mirror these values.
export const BRAND = {
  purple: '#782B90',
  yellow: '#FFF200',
} as const

export const MAP = {
  boundary: '#782B90',
  boundaryFill: '#782B90',
  cell: '#782B90',
  selected: '#FFF200',
  store: '#782B90',
} as const

// Sequential ramps (one hue, light to dark) for the Opportunity Finder map: lightness rises monotonically with the value, so
// the map stays readable in greyscale and for colour-blind users. Unranked cells use a neutral grey, never a ramp colour.
type RGB = [number, number, number]
const mix = (a: RGB, b: RGB, t: number) => `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * Math.min(1, Math.max(0, t)))).join(',')})`
export const OPP = {
  ramp: (t: number) => mix([222, 200, 232], [63, 14, 80], t),        // lilac -> deep purple (score percentile)
  rampCoverage: (t: number) => mix([214, 236, 233], [8, 84, 80], t), // pale teal -> deep teal (scouting coverage)
  unranked: '#b9b3bd',
  top: '#FFF200',
  selected: '#221a26',
} as const
