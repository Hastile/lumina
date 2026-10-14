// Renders a graphic clip's text and shape layers in sequence pixel space,
// bottom to top. The engine (lumina/render/text.py, compositor.render_graphic)
// draws the same layers with the same layout rules for export.

import type { Graphic, ShapeLayer, TextLayer, TextRun } from '../engine/document'
import { svgPath } from '../util/shapes'

const LINE_HEIGHT = 1.2
type Run = { [K in keyof Omit<TextRun, 'text' | 'stroke_color'>]-?: NonNullable<TextRun[K]> } & { text: string; stroke_color: string | null }

/** The layer's runs with unset fields inherited from the layer (text.py resolved_runs). */
export function resolvedRuns(l: TextLayer): Run[] {
  const base = {
    font_family: l.font_family, font_size: l.font_size, bold: l.bold, italic: l.italic, color: l.color,
    stroke_color: l.stroke_color, stroke_width: l.stroke_width, tracking: l.tracking ?? 0
  }
  const runs = l.runs?.length ? l.runs : [{ text: l.text }]
  return runs.map((r) => {
    const out: Record<string, unknown> = { ...base }
    for (const [k, v] of Object.entries(r)) if (v !== null && v !== undefined) out[k] = v
    return out as Run
  })
}

function splitLines(runs: Run[]): Run[][] {
  const lines: Run[][] = [[]]
  for (const r of runs) {
    r.text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').forEach((part, i) => {
      if (i) lines.push([])
      if (part) lines[lines.length - 1].push({ ...r, text: part })
    })
  }
  return lines
}

function shadowCss(l: TextLayer): string | undefined {
  const sh = l.shadow
  if (!sh) return undefined
  const a = (sh.angle * Math.PI) / 180
  const dx = sh.distance * Math.sin(a)
  const dy = -sh.distance * Math.cos(a)
  const hex = sh.color.replace('#', '')
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) || 0)
  // drop-shadow shadows the text's silhouette (stroke included) like text.py; text-shadow would paint over the stroke
  return `drop-shadow(${Math.round(dx)}px ${Math.round(dy)}px ${sh.blur}px rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, sh.opacity / 100))}))`
}

function spanStyle(r: Run, lineHeight: number): React.CSSProperties {
  ensureFont(r.font_family)
  const stroke = r.stroke_color && r.stroke_width ? `${r.stroke_width * 2}px ${r.stroke_color}` : undefined
  return {
    fontFamily: `"${r.font_family}", "Pretendard", "Malgun Gothic", sans-serif`,
    fontSize: r.font_size,
    fontWeight: r.bold ? 700 : undefined,
    fontStyle: r.italic ? 'italic' : undefined,
    color: r.color,
    letterSpacing: r.tracking ? `${r.tracking / 1000}em` : undefined,
    WebkitTextStroke: stroke,
    paintOrder: 'stroke fill',
    lineHeight: `${lineHeight}px`
  }
}

/** Lines of styled spans sharing a baseline (flex baseline alignment, like text.py's layout). */
export function TextBlock({ l }: { l: TextLayer }) {
  const flex = l.align === 'left' ? 'flex-start' : l.align === 'right' ? 'flex-end' : 'center'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: flex, whiteSpace: 'pre', filter: shadowCss(l) }}>
      {splitLines(resolvedRuns(l)).map((line, i) => {
        const size = Math.max(...(line.length ? line.map((r) => r.font_size) : [l.font_size]))
        const lh = LINE_HEIGHT * size
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'baseline', minHeight: lh }}>
            {line.map((r, j) => (
              <span key={j} style={spanStyle(r, lh)}>
                {r.text}
              </span>
            ))}
          </div>
        )
      })}
    </div>
  )
}

const loaded = new Set<string>()

/** Premiere stores PostScript names (e.g. "S-CoreDream-9Black"); CSS local() can resolve them. */
function ensureFont(family: string): void {
  if (loaded.has(family)) return
  loaded.add(family)
  const style = document.createElement('style')
  const safe = family.replace(/["\\]/g, '')
  style.textContent = `@font-face { font-family: "${safe}"; src: local("${safe}"), local("${safe.replace(/-/g, ' ')}"); }`
  document.head.appendChild(style)
}

/** Outer box of a text layer: point text is positioned by its alignment point at
 * the block's vertical center; area text by its box's top-left. */
export function textStyle(l: TextLayer): React.CSSProperties {
  ensureFont(l.font_family)
  const common: React.CSSProperties = {
    position: 'absolute',
    left: l.position[0],
    top: l.position[1],
    opacity: l.opacity / 100,
    pointerEvents: 'auto'
  }
  if (l.box) {
    const v = l.vertical_align === 'center' ? 'center' : l.vertical_align === 'bottom' ? 'flex-end' : 'flex-start'
    const h = l.align === 'left' ? 'flex-start' : l.align === 'right' ? 'flex-end' : 'center'
    return {
      ...common,
      width: l.box[0],
      height: l.box[1],
      display: 'flex',
      flexDirection: 'column',
      justifyContent: v,
      alignItems: h,
      transform: `rotate(${l.rotation}deg) scale(${l.scale / 100})`,
      transformOrigin: '0 0'
    }
  }
  const alignX = l.align === 'left' ? '0%' : l.align === 'right' ? '-100%' : '-50%'
  return {
    ...common,
    transform: `translate(${alignX}, -50%) rotate(${l.rotation}deg) scale(${l.scale / 100})`,
    transformOrigin: `${l.align === 'left' ? '0%' : l.align === 'right' ? '100%' : '50%'} 50%`,
    background: l.background ?? undefined,
    padding: l.background ? `${0.08 * l.font_size}px ${0.3 * l.font_size}px` : undefined
  }
}

/** The anchor (layer px) lands on position; scale and rotation apply around it. */
function ShapeView({ l }: { l: ShapeLayer }) {
  const sx = (l.uniform ? l.scale : l.scale_width) / 100
  const sy = l.scale / 100
  const stroke = l.stroke_color && l.stroke_width > 0
  return (
    <svg
      data-layer={l.id}
      width={1}
      height={1}
      style={{
        position: 'absolute',
        left: l.position[0] - l.anchor[0],
        top: l.position[1] - l.anchor[1],
        overflow: 'visible',
        transformOrigin: `${l.anchor[0]}px ${l.anchor[1]}px`,
        transform: `rotate(${l.rotation}deg) scale(${sx}, ${sy})`,
        opacity: l.opacity / 100,
        mixBlendMode: l.blend === 'multiply' ? 'multiply' : undefined,
        pointerEvents: 'none'
      }}
    >
      <path
        d={svgPath(l.path, l.closed)}
        fill={l.fill && l.closed ? l.fill : 'none'}
        stroke={stroke ? l.stroke_color! : 'none'}
        strokeWidth={stroke ? l.stroke_width : 0}
        strokeLinejoin="round"
        style={{ pointerEvents: 'visiblePainted' }}
      />
    </svg>
  )
}

export function GraphicView({ graphic }: { graphic: Graphic }) {
  return (
    <>
      {graphic.layers.map((l) =>
        l.visible === false ? null : l.type === 'shape' ? (
          <ShapeView key={l.id} l={l} />
        ) : (
          <div key={l.id} data-layer={l.id} style={textStyle(l)}>
            <TextBlock l={l} />
          </div>
        )
      )}
    </>
  )
}
