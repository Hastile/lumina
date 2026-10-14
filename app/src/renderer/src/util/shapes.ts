// TS twin of engine/lumina/shapes.py: vertices are [x, y, in_x, in_y, out_x, out_y]
// in layer pixels, tangent handles as absolute points.

export type Vertex = number[]

/** SVG path data; straight segments become lines so rectangles stay crisp. */
export function svgPath(path: Vertex[], closed: boolean): string {
  if (!path.length) return ''
  const n = path.length
  let d = `M${path[0][0]} ${path[0][1]}`
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    const a = path[i]
    const b = path[(i + 1) % n]
    const straight = a[4] === a[0] && a[5] === a[1] && b[2] === b[0] && b[3] === b[1]
    d += straight ? ` L${b[0]} ${b[1]}` : ` C${a[4]} ${a[5]} ${b[2]} ${b[3]} ${b[0]} ${b[1]}`
  }
  return closed ? d + ' Z' : d
}

/** Bounding box of the vertices: [x0, y0, x1, y1]. */
export function bounds(path: Vertex[]): [number, number, number, number] {
  if (!path.length) return [0, 0, 0, 0]
  const xs = path.map((v) => v[0])
  const ys = path.map((v) => v[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}
