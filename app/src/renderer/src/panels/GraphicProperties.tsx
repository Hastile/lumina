// Properties panel for a graphic clip, laid out like Premiere 2025's: the layer list on top, then
// with no layer selected the clip's own transform (Clip Transform), and with a layer selected that
// layer's Text, Appearance, Align and Transform and Text Styles. Only what Lumina's graphics hold
// is shown (no kerning/leading, text masks or responsive design).
import { useEffect, useState } from 'react'
import {
  AlignCenter, AlignCenterHorizontal, AlignCenterVertical, AlignLeft, AlignRight, AlignVerticalJustifyCenter, AlignVerticalJustifyEnd,
  AlignVerticalJustifyStart, ArrowDown, ArrowUp, Bold, ChevronDown, Circle, Eye, EyeOff, Italic, Plus, Spline, Square, Trash2, TriangleAlert, Type
} from 'lucide-react'
import { t } from '@shared/i18n'
import type { ShapeLayer, TextLayer } from '../engine/document'
import { exec, set, useStore } from '../state/store'
import { layerName, ShapeLayerEditor } from './GraphicLayers'
import { NumberScrub, type ClipCtx } from './PropertyControls'
import { TextStyleChips } from './TrackPanel'

type Layer = TextLayer | ShapeLayer
type Shadow = NonNullable<TextLayer['shadow']>

const FONTS = ['Pretendard', 'Pretendard-Medium', 'Pretendard-Bold', 'Pretendard-Black', 'Malgun Gothic', 'S-CoreDream-5Medium', 'S-CoreDream-6Bold', 'S-CoreDream-9Black', 'Noto Sans KR', 'Arial', 'Impact']
const SHADOW: Shadow = { color: '#000000', opacity: 75, angle: 135, distance: 4, blur: 4 }

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="lm-prop-section">
      <div className="head">
        <ChevronDown size={13} />
        <span>{title}</span>
      </div>
      {children}
    </div>
  )
}

/** The graphic's layers, top-most first: click one to edit it, click it again (or the empty area) for the clip. */
function LayerList({ c, selected }: { c: ClipCtx; selected: string | null }) {
  const layers = c.clip.graphic!.layers
  const base = { sequence_id: c.seq.id, clip_id: c.clip.id }
  const index = layers.findIndex((l) => l.id === selected)
  const pick = (id: string | null) => set({ selectedLayer: id })
  const setVisible = (l: Layer, visible: boolean) =>
    void exec(l.type === 'shape' ? 'graphic.set_shape' : 'graphic.set_layer', { ...base, layer_id: l.id, visible }, t('Toggle Layer Visibility'))
  const added = (r: { layer_id: string } | null) => r && pick(r.layer_id)
  return (
    <>
      <div className="lm-glist" onClick={(e) => e.target === e.currentTarget && pick(null)}>
        {layers
          .map((l, i) => ({ l, i }))
          .reverse()
          .map(({ l }) => {
            const Icon = l.type === 'text' ? Type : l.shape === 'ellipse' ? Circle : l.shape === 'rectangle' ? Square : Spline
            const visible = l.visible !== false
            return (
              <div key={l.id} className={`lm-glayer ${visible ? '' : 'hidden'} ${l.id === selected ? 'selected' : ''}`} data-glayer={l.id} onClick={() => pick(l.id === selected ? null : l.id)}>
                <div className="head">
                  <button className="lm-iconbtn" title={t('Toggle Layer Visibility')} onClick={(e) => { e.stopPropagation(); setVisible(l, !visible) }}>
                    {visible ? <Eye size={12} /> : <EyeOff size={12} />}
                  </button>
                  <Icon size={12} />
                  <span className="name">{layerName(l)}</span>
                </div>
              </div>
            )
          })}
        {!layers.length && <div className="lm-empty small">{t('No layers yet: add text or a shape.')}</div>}
      </div>
      <div className="lm-glist-tools">
        <button className="lm-btn small" data-add-layer="text" onClick={async () => added(await exec<{ layer_id: string }>('graphic.add_layer', { ...base, text: t('New Text Layer') }, t('New Layer > Text')))}>
          <Plus size={12} /> {t('Text')}
        </button>
        <button className="lm-btn small" data-add-shape="rectangle" onClick={async () => added(await exec<{ layer_id: string }>('graphic.add_shape', { ...base, shape: 'rectangle' }, t('New Layer > Rectangle')))}>
          <Square size={12} /> {t('Rectangle')}
        </button>
        <button className="lm-btn small" onClick={async () => added(await exec<{ layer_id: string }>('graphic.add_shape', { ...base, shape: 'ellipse' }, t('New Layer > Ellipse')))}>
          <Circle size={12} /> {t('Ellipse')}
        </button>
        <span style={{ flex: 1 }} />
        <button className="lm-iconbtn" title={t('Bring Forward')} disabled={index < 0 || index === layers.length - 1}
          onClick={() => void exec('graphic.move_layer', { ...base, layer_id: selected, index: index + 1 }, t('Arrange'))}>
          <ArrowUp size={12} />
        </button>
        <button className="lm-iconbtn" title={t('Send Backward')} disabled={index <= 0}
          onClick={() => void exec('graphic.move_layer', { ...base, layer_id: selected, index: index - 1 }, t('Arrange'))}>
          <ArrowDown size={12} />
        </button>
        <button className="lm-iconbtn" title={t('Delete Layer')} disabled={index < 0}
          onClick={async () => (await exec('graphic.remove_layer', { ...base, layer_id: selected }, t('Delete Layer'))) !== null && pick(null)}>
          <Trash2 size={12} />
        </button>
      </div>
    </>
  )
}

function TextSections({ c, layer }: { c: ClipCtx; layer: TextLayer }) {
  const [text, setText] = useState(layer.text)
  useEffect(() => setText(layer.text), [layer.text, layer.id])
  const set = (changes: Record<string, unknown>, label: string) =>
    void exec('graphic.set_layer', { sequence_id: c.seq.id, clip_id: c.clip.id, layer_id: layer.id, ...changes }, label)
  const fonts = FONTS.includes(layer.font_family) ? FONTS : [layer.font_family, ...FONTS]
  const { width: W, height: H } = c.seq.settings
  const box = layer.box
  const sh = layer.shadow
  const setShadow = (v: Partial<Shadow>) => sh && set({ shadow: { ...sh, ...v } }, t('Shadow'))
  // centring needs the block's extent: an area text box, or point text anchored at its centre
  const cx = box ? (W - box[0]) / 2 : layer.align === 'center' ? W / 2 : null
  const cy = box ? (H - box[1]) / 2 : H / 2
  return (
    <>
      <Section title={t('Text')}>
        <div className="lm-text-layer" data-text-layer={layer.id}>
          {layer.approximate && (
            <div className="warn">
              <TriangleAlert size={12} /> {t('Approximate styling imported from Premiere: check font size and position.')}
            </div>
          )}
          <textarea
            value={text}
            rows={Math.min(5, Math.max(1, text.split('\n').length))}
            onChange={(e) => setText(e.target.value)}
            onBlur={() => text !== layer.text && set({ text }, t('Source Text'))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) (e.target as HTMLTextAreaElement).blur()
            }}
          />
          <div className="row">
            <select value={layer.font_family} onChange={(e) => set({ font_family: e.target.value }, t('Font'))} title={t('Font')}>
              {fonts.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
            <button className={`lm-iconbtn ${layer.bold ? 'on' : ''}`} title={t('Bold')} onClick={() => set({ bold: !layer.bold }, t('Bold'))}>
              <Bold size={13} />
            </button>
            <button className={`lm-iconbtn ${layer.italic ? 'on' : ''}`} title="Italic" onClick={() => set({ italic: !layer.italic }, 'Italic')}>
              <Italic size={13} />
            </button>
          </div>
          <div className="row">
            <span className="k">{t('Font Size')}</span>
            <input className="lm-range" type="range" min={1} max={400} value={Math.min(400, layer.font_size)} onChange={(e) => set({ font_size: Number(e.target.value) }, t('Font Size'))} />
            <NumberScrub value={layer.font_size} min={1} max={2000} digits={0} onChange={(v) => set({ font_size: v }, t('Font Size'))} />
          </div>
          <div className="row">
            {(['left', 'center', 'right'] as const).map((a) => {
              const Icon = a === 'left' ? AlignLeft : a === 'center' ? AlignCenter : AlignRight
              return (
                <button key={a} className={`lm-iconbtn ${layer.align === a ? 'on' : ''}`} title={t('Alignment')} onClick={() => set({ align: a }, t('Alignment'))}>
                  <Icon size={13} />
                </button>
              )
            })}
            {box && <span className="sep" />}
            {box &&
              (['top', 'center', 'bottom'] as const).map((v) => {
                const Icon = v === 'top' ? AlignVerticalJustifyStart : v === 'center' ? AlignVerticalJustifyCenter : AlignVerticalJustifyEnd
                return (
                  <button key={v} className={`lm-iconbtn ${layer.vertical_align === v ? 'on' : ''}`} title={t('Alignment')} onClick={() => set({ vertical_align: v }, t('Alignment'))}>
                    <Icon size={13} />
                  </button>
                )
              })}
            <span style={{ flex: 1 }} />
            <span className="k">{t('Tracking')}</span>
            <NumberScrub value={layer.tracking ?? 0} min={-1000} max={10000} digits={0} onChange={(v) => set({ tracking: v }, t('Tracking'))} />
          </div>
          <div className="row">
            <label className="swatch">
              <input type="checkbox" checked={!!box} data-area-text=""
                onChange={(e) => set(e.target.checked ? { box: [Math.round(W * 0.75), Math.round(H * 0.3)] } : { box: null }, t('Area Text'))} />
              {t('Area Text')}
            </label>
            {box && <NumberScrub value={box[0]} min={1} digits={0} suffix=" W" onChange={(v) => set({ box: [Math.max(1, v), box[1]] }, t('Area Text'))} />}
            {box && <NumberScrub value={box[1]} min={1} digits={0} suffix=" H" onChange={(v) => set({ box: [box[0], Math.max(1, v)] }, t('Area Text'))} />}
          </div>
        </div>
      </Section>
      <Section title={t('Appearance')}>
        <div className="lm-text-layer">
          <div className="row">
            <label className="swatch">
              <input type="color" value={layer.color.slice(0, 7)} onChange={(e) => set({ color: e.target.value }, t('Fill Color'))} />
              {t('Fill Color')}
            </label>
          </div>
          <div className="row">
            <label className="swatch">
              <input type="checkbox" checked={!!layer.stroke_color && layer.stroke_width > 0}
                onChange={(e) => set(e.target.checked ? { stroke_color: layer.stroke_color ?? '#000000', stroke_width: layer.stroke_width || 4 } : { stroke_width: 0 }, t('Stroke'))} />
              <input type="color" value={(layer.stroke_color ?? '#000000').slice(0, 7)} onChange={(e) => set({ stroke_color: e.target.value, stroke_width: layer.stroke_width || 4 }, t('Stroke'))} />
              {t('Stroke')}
            </label>
            <NumberScrub value={layer.stroke_width} min={0} max={200} digits={1} onChange={(v) => set({ stroke_width: v, stroke_color: layer.stroke_color ?? '#000000' }, t('Stroke Width'))} />
          </div>
          <div className="row">
            <label className="swatch">
              <input type="checkbox" checked={!!layer.background} onChange={(e) => set({ background: e.target.checked ? '#000000' : null }, t('Background'))} />
              <input type="color" value={(layer.background ?? '#000000').slice(0, 7)} onChange={(e) => set({ background: e.target.value }, t('Background'))} />
              {t('Background')}
            </label>
          </div>
          <div className="row">
            <label className="swatch">
              <input type="checkbox" checked={!!sh} onChange={(e) => set({ shadow: e.target.checked ? SHADOW : null }, t('Shadow'))} />
              <input type="color" value={(sh?.color ?? '#000000').slice(0, 7)} disabled={!sh} onChange={(e) => setShadow({ color: e.target.value })} />
              {t('Shadow')}
            </label>
          </div>
          {sh && (
            <div className="row indent">
              <span className="k">{t('Opacity')}</span>
              <NumberScrub value={sh.opacity} min={0} max={100} digits={0} suffix=" %" onChange={(v) => setShadow({ opacity: v })} />
              <span className="k">{t('Angle')}</span>
              <NumberScrub value={sh.angle} digits={0} suffix=" °" onChange={(v) => setShadow({ angle: v })} />
              <span className="k">{t('Distance')}</span>
              <NumberScrub value={sh.distance} min={0} max={500} digits={1} onChange={(v) => setShadow({ distance: v })} />
              <span className="k">{t('Blur')}</span>
              <NumberScrub value={sh.blur} min={0} max={200} digits={1} onChange={(v) => setShadow({ blur: v })} />
            </div>
          )}
        </div>
      </Section>
      <Section title={t('Align and Transform')}>
        <div className="lm-text-layer">
          <div className="row">
            <span className="k">{t('Align')}</span>
            <button className="lm-iconbtn" title={t('Center Horizontally')} disabled={cx === null} onClick={() => cx !== null && set({ position: [cx, layer.position[1]] }, t('Center Horizontally'))}>
              <AlignCenterHorizontal size={13} />
            </button>
            <button className="lm-iconbtn" title={t('Center Vertically')} onClick={() => set({ position: [layer.position[0], cy] }, t('Center Vertically'))}>
              <AlignCenterVertical size={13} />
            </button>
          </div>
          <div className="row">
            <span className="k">{t('Position')}</span>
            <NumberScrub value={layer.position[0]} digits={1} suffix=" X" onChange={(v) => set({ position: [v, layer.position[1]] }, t('Position'))} />
            <NumberScrub value={layer.position[1]} digits={1} suffix=" Y" onChange={(v) => set({ position: [layer.position[0], v] }, t('Position'))} />
          </div>
          <div className="row">
            <span className="k">{t('Scale')}</span>
            <NumberScrub value={layer.scale} min={0} max={10000} digits={0} suffix=" %" onChange={(v) => set({ scale: v }, t('Scale'))} />
          </div>
          <div className="row">
            <span className="k">{t('Rotation')}</span>
            <NumberScrub value={layer.rotation} digits={0} suffix=" °" onChange={(v) => set({ rotation: v }, t('Rotation'))} />
          </div>
          <div className="row">
            <span className="k">{t('Opacity')}</span>
            <NumberScrub value={layer.opacity} min={0} max={100} digits={0} suffix=" %" onChange={(v) => set({ opacity: v }, t('Opacity'))} />
          </div>
        </div>
      </Section>
      <Section title={t('Text Styles')}>
        <div className="lm-text-layer">
          <TextStyleChips onApply={(id, name) => void exec('graphic.apply_style', { sequence_id: c.seq.id, clip_ids: [c.clip.id], style_id: id }, `${t('Text Styles')}: ${name}`)} />
          <span className="k small">{t('Text Styles apply to every text layer of the clip.')}</span>
        </div>
      </Section>
    </>
  )
}

/** Properties of a graphic clip: ``clipSections`` (the clip's own Clip Transform and Crop) show when no layer is selected. */
export function GraphicProperties({ c, clipSections }: { c: ClipCtx; clipSections: React.ReactNode }) {
  const selectedLayer = useStore((s) => s.selectedLayer)
  const layer = c.clip.graphic!.layers.find((l) => l.id === selectedLayer) ?? null
  return (
    <>
      <LayerList c={c} selected={layer?.id ?? null} />
      {!layer && clipSections}
      {layer?.type === 'text' && <TextSections c={c} layer={layer} />}
      {layer?.type === 'shape' && (
        <Section title={t('Appearance')}>
          <ShapeLayerEditor c={c} layer={layer} />
        </Section>
      )}
    </>
  )
}
