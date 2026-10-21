// A graphic clip's layer stack (Essential Graphics style: top-most first) with
// the eye toggle, stacking order and per-type editors.
import { ArrowDown, ArrowUp, Circle, Eye, EyeOff, Image as ImageIcon, Maximize, Minimize, Spline, Square, Trash2, Type } from 'lucide-react'
import { t } from '@shared/i18n'
import type { ImageLayer, ShapeLayer, TextLayer } from '../engine/document'
import { exec } from '../state/store'
import { bounds } from '../util/shapes'
import { NumberScrub, type ClipCtx } from './PropertyControls'
import { TextLayerEditor } from './TextLayerEditor'

type Layer = TextLayer | ShapeLayer | ImageLayer

/** The command that edits a layer of this type. */
export function layerCommand(l: Layer): string {
  return l.type === 'shape' ? 'graphic.set_shape' : l.type === 'image' ? 'graphic.set_image' : 'graphic.set_layer'
}

export function LayerIcon({ l, size = 12 }: { l: Layer; size?: number }) {
  const Icon = l.type === 'text' ? Type : l.type === 'image' ? ImageIcon : l.shape === 'ellipse' ? Circle : l.shape === 'rectangle' ? Square : Spline
  return <Icon size={size} />
}

/** Image file filters for the open dialog. */
export const IMAGE_FILTERS = [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff'] }]

export function layerName(l: Layer): string {
  if (l.type === 'image') return l.path.split(/[\\/]/).pop() || t('Image')
  if (l.type === 'shape') return t(l.shape === 'ellipse' ? 'Ellipse' : l.shape === 'rectangle' ? 'Rectangle' : 'Path')
  return l.text.split('\n')[0] || t('Text')
}

export function GraphicLayers({ c, compact = false }: { c: ClipCtx; compact?: boolean }) {
  const layers = c.clip.graphic!.layers
  const base = { sequence_id: c.seq.id, clip_id: c.clip.id }
  const setVisible = (l: Layer, visible: boolean) =>
    void exec(layerCommand(l), { ...base, layer_id: l.id, visible }, t('Toggle Layer Visibility'))
  const move = (l: Layer, index: number) => void exec('graphic.move_layer', { ...base, layer_id: l.id, index }, t('Arrange'))
  return (
    <div className="lm-glayers">
      {layers
        .map((l, i) => ({ l, i }))
        .reverse()
        .map(({ l, i }) => {
          const visible = l.visible !== false
          return (
            <div key={l.id} className={`lm-glayer ${visible ? '' : 'hidden'}`} data-glayer={l.id}>
              <div className="head">
                <button className="lm-iconbtn" title={t('Toggle Layer Visibility')} onClick={() => setVisible(l, !visible)}>
                  {visible ? <Eye size={12} /> : <EyeOff size={12} />}
                </button>
                <LayerIcon l={l} />
                <span className="name">{layerName(l)}</span>
                <span style={{ flex: 1 }} />
                <button className="lm-iconbtn" title={t('Bring Forward')} disabled={i === layers.length - 1} onClick={() => move(l, i + 1)}>
                  <ArrowUp size={12} />
                </button>
                <button className="lm-iconbtn" title={t('Send Backward')} disabled={i === 0} onClick={() => move(l, i - 1)}>
                  <ArrowDown size={12} />
                </button>
                {layers.length > 1 && (
                  <button className="lm-iconbtn" title={t('Delete Layer')} onClick={() => void exec('graphic.remove_layer', { ...base, layer_id: l.id }, t('Delete Layer'))}>
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
              {l.type === 'shape' ? (
                <ShapeLayerEditor c={c} layer={l} compact={compact} />
              ) : l.type === 'image' ? (
                <ImageLayerEditor c={c} layer={l} compact={compact} />
              ) : (
                <TextLayerEditor c={c} layer={l} compact={compact} />
              )}
            </div>
          )
        })}
    </div>
  )
}

export function ShapeLayerEditor({ c, layer, compact = false }: { c: ClipCtx; layer: ShapeLayer; compact?: boolean }) {
  const set = (changes: Record<string, unknown>, label: string) =>
    void exec('graphic.set_shape', { sequence_id: c.seq.id, clip_id: c.clip.id, layer_id: layer.id, ...changes }, label)
  const [x0, y0, x1, y1] = bounds(layer.path)
  const stroked = !!layer.stroke_color && layer.stroke_width > 0
  return (
    <div className={`lm-text-layer ${compact ? 'compact' : ''}`} data-shape-editor={layer.id}>
      <div className="row">
        <label className="swatch">
          <input type="checkbox" checked={!!layer.fill} onChange={(e) => set({ fill_enabled: e.target.checked }, t('Fill'))} />
          <input type="color" value={layer.fill ?? '#ffffff'} onChange={(e) => set({ fill: e.target.value }, t('Fill'))} />
          {t('Fill')}
        </label>
        <label className="swatch">
          <input
            type="checkbox"
            checked={stroked}
            onChange={(e) => set(e.target.checked ? { stroke_color: layer.stroke_color ?? '#000000', stroke_width: layer.stroke_width || 4 } : { stroke_width: 0 }, t('Stroke'))}
          />
          <input type="color" value={layer.stroke_color ?? '#000000'} onChange={(e) => set({ stroke_color: e.target.value, stroke_width: layer.stroke_width || 4 }, t('Stroke'))} />
          {t('Stroke')}
        </label>
        <NumberScrub value={layer.stroke_width} min={0} max={500} digits={0} onChange={(v) => set({ stroke_width: v, stroke_color: layer.stroke_color ?? '#000000' }, t('Stroke Width'))} />
      </div>
      <div className="row">
        <span className="k">{t('Size')}</span>
        <NumberScrub value={x1 - x0} min={1} digits={0} suffix=" W" onChange={(v) => set({ width: Math.max(1, v) }, t('Size'))} />
        <NumberScrub value={y1 - y0} min={1} digits={0} suffix=" H" onChange={(v) => set({ height: Math.max(1, v) }, t('Size'))} />
      </div>
      {!compact && (
        <>
          <div className="row">
            <span className="k">{t('Position')}</span>
            <NumberScrub value={layer.position[0]} digits={0} onChange={(v) => set({ position: [v, layer.position[1]] }, t('Position'))} />
            <NumberScrub value={layer.position[1]} digits={0} onChange={(v) => set({ position: [layer.position[0], v] }, t('Position'))} />
          </div>
          <div className="row">
            <span className="k">{t('Rotation')}</span>
            <NumberScrub value={layer.rotation} digits={0} suffix=" °" onChange={(v) => set({ rotation: v }, t('Rotation'))} />
            <span className="k">{t('Opacity')}</span>
            <NumberScrub value={layer.opacity} min={0} max={100} digits={0} suffix=" %" onChange={(v) => set({ opacity: v }, t('Opacity'))} />
          </div>
        </>
      )}
    </div>
  )
}

/** An image layer: its file, and its placement (the anchor, default the centre, lands on the position). */
export function ImageLayerEditor({ c, layer, compact = false }: { c: ClipCtx; layer: ImageLayer; compact?: boolean }) {
  const set = (changes: Record<string, unknown>, label: string) =>
    void exec('graphic.set_image', { sequence_id: c.seq.id, clip_id: c.clip.id, layer_id: layer.id, ...changes }, label)
  const { width: W, height: H } = c.seq.settings
  const [w, h] = layer.size
  const anchor = layer.anchor ?? [w / 2, h / 2]
  const frame = (mode: 'fit' | 'fill') => {
    const k = Math.round((mode === 'fit' ? Math.min : Math.max)(W / w, H / h) * 10000) / 100
    set({ scale: k, scale_width: k, uniform: true, anchor: null, position: [W / 2, H / 2] }, t(mode === 'fit' ? 'Fit' : 'Fill'))
  }
  const replace = async () => {
    const [path] = await window.lumina.openDialog({ title: t('Replace Image...'), properties: ['openFile'], filters: IMAGE_FILTERS }, 'import')
    if (path) set({ path }, t('Replace Image...'))
  }
  return (
    <div className={`lm-text-layer ${compact ? 'compact' : ''}`} data-image-editor={layer.id}>
      <div className="row">
        <span className="k file" title={layer.path}>{layerName(layer)}</span>
        <span className="k">{Math.round(w)} x {Math.round(h)}</span>
        <span style={{ flex: 1 }} />
        <button className="lm-btn small" onClick={() => void replace()}>{t('Replace Image...')}</button>
      </div>
      <div className="row">
        <button className="lm-chip" onClick={() => frame('fill')}>
          <Maximize size={12} /> {t('Fill')}
        </button>
        <button className="lm-chip" onClick={() => frame('fit')}>
          <Minimize size={12} /> {t('Fit')}
        </button>
      </div>
      <div className="row">
        <span className="k">{t('Position')}</span>
        <NumberScrub value={layer.position[0]} digits={1} suffix=" X" onChange={(v) => set({ position: [v, layer.position[1]] }, t('Position'))} />
        <NumberScrub value={layer.position[1]} digits={1} suffix=" Y" onChange={(v) => set({ position: [layer.position[0], v] }, t('Position'))} />
      </div>
      {!compact && (
        <div className="row">
          <span className="k">{t('Anchor Point')}</span>
          <NumberScrub value={anchor[0]} digits={1} suffix=" X" onChange={(v) => set({ anchor: [v, anchor[1]] }, t('Anchor Point'))} />
          <NumberScrub value={anchor[1]} digits={1} suffix=" Y" onChange={(v) => set({ anchor: [anchor[0], v] }, t('Anchor Point'))} />
        </div>
      )}
      <div className="row">
        <span className="k">{t('Scale')}</span>
        <NumberScrub value={layer.scale} min={0} max={10000} digits={1} suffix=" %" onChange={(v) => set({ scale: v }, t('Scale'))} />
        {!layer.uniform && <NumberScrub value={layer.scale_width} min={0} max={10000} digits={1} suffix=" % W" onChange={(v) => set({ scale_width: v }, t('Scale Width'))} />}
        <label className="swatch">
          <input type="checkbox" checked={layer.uniform} onChange={(e) => set({ uniform: e.target.checked, scale_width: layer.scale }, t('Uniform Scale'))} />
          {t('Uniform Scale')}
        </label>
      </div>
      <div className="row">
        <span className="k">{t('Rotation')}</span>
        <NumberScrub value={layer.rotation} digits={0} suffix=" °" onChange={(v) => set({ rotation: v }, t('Rotation'))} />
        <span className="k">{t('Opacity')}</span>
        <NumberScrub value={layer.opacity} min={0} max={100} digits={0} suffix=" %" onChange={(v) => set({ opacity: v }, t('Opacity'))} />
      </div>
      <div className="row" data-image-crop={layer.id}>
        <span className="k">{t('Crop')}</span>
        {CROPS.map(([k, label, other]) => (
          <NumberScrub
            key={k}
            value={layer[k]}
            min={0}
            // the opposite side's crop leaves at least 1% of the picture
            max={Math.max(0, 99 - layer[other])}
            digits={1}
            suffix={` % ${label}`}
            onChange={(v) => set({ [k]: v }, t('Crop'))}
          />
        ))}
        {(layer.crop_left || layer.crop_top || layer.crop_right || layer.crop_bottom) ? (
          <button className="lm-btn small" onClick={() => set({ crop_left: 0, crop_top: 0, crop_right: 0, crop_bottom: 0 }, t('Reset'))}>{t('Reset')}</button>
        ) : null}
      </div>
    </div>
  )
}

type CropKey = 'crop_left' | 'crop_top' | 'crop_right' | 'crop_bottom'
const CROPS: [CropKey, string, CropKey][] = [
  ['crop_left', 'L', 'crop_right'],
  ['crop_top', 'T', 'crop_bottom'],
  ['crop_right', 'R', 'crop_left'],
  ['crop_bottom', 'B', 'crop_top']
]
