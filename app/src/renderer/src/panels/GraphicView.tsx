// Renders a graphic clip's text and shape layers in sequence pixel space,
// bottom to top. The engine compositor (render_graphic) draws the same layers
// for export, so preview == output.

import type { Graphic, ShapeLayer, TextLayer } from '../engine/document'
import { svgPath } from '../util/shapes'

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

export function textStyle(l: TextLayer): React.CSSProperties {
  ensureFont(l.font_family)
  const alignX = l.align === 'left' ? '0%' : l.align === 'right' ? '-100%' : '-50%'
  return {
    position: 'absolute',
    left: l.position[0],
    top: l.position[1],
    transform: `translate(${alignX}, -50%) rotate(${l.rotation}deg) scale(${l.scale / 100})`,
    transformOrigin: `${l.align === 'left' ? '0%' : l.align === 'right' ? '100%' : '50%'} 50%`,
    whiteSpace: 'pre',
    textAlign: l.align,
    fontFamily: `"${l.font_family}", "Pretendard", "Malgun Gothic", sans-serif`,
    fontSize: l.font_size,
    fontWeight: l.bold ? 700 : undefined,
    fontStyle: l.italic ? 'italic' : undefined,
    lineHeight: 1.2,
    color: l.color,
    WebkitTextStroke: l.stroke_color && l.stroke_width ? `${l.stroke_width * 2}px ${l.stroke_color}` : undefined,
    paintOrder: 'stroke fill',
    background: l.background ?? undefined,
    padding: l.background ? '0.08em 0.3em' : undefined,
    opacity: l.opacity / 100,
    pointerEvents: 'auto'
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
            {l.text}
          </div>
        )
      )}
    </>
  )
}
