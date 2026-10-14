// Renders a graphic clip's text layers in sequence pixel space. The same DOM is
// what the headless export renderer will capture, so preview == output.

import type { Graphic, TextLayer } from '../engine/document'

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

export function GraphicView({ graphic }: { graphic: Graphic }) {
  return (
    <>
      {graphic.layers.map((l) => (
        <div key={l.id} data-layer={l.id} style={textStyle(l)}>
          {l.text}
        </div>
      ))}
    </>
  )
}
