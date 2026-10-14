// Text layer editing (Properties panel / Effect Controls "Graphic Parameters").
import { useEffect, useState } from 'react'
import { AlignCenter, AlignLeft, AlignRight, Bold, Italic, Trash2, TriangleAlert } from 'lucide-react'
import { t } from '@shared/i18n'
import type { TextLayer } from '../engine/document'
import { exec } from '../state/store'
import { NumberScrub, type ClipCtx } from './PropertyControls'

const FONTS = ['Pretendard', 'Pretendard-Bold', 'Pretendard-Black', 'Malgun Gothic', 'S-CoreDream-6Bold', 'S-CoreDream-9Black', 'Noto Sans KR', 'Arial', 'Impact']

export function TextLayerEditor({ c, layer, compact = false }: { c: ClipCtx; layer: TextLayer; compact?: boolean }) {
  const [text, setText] = useState(layer.text)
  useEffect(() => setText(layer.text), [layer.text])
  const set = (changes: Record<string, unknown>, label: string) =>
    void exec('graphic.set_layer', { sequence_id: c.seq.id, clip_id: c.clip.id, layer_id: layer.id, ...changes }, label)
  const fonts = FONTS.includes(layer.font_family) ? FONTS : [layer.font_family, ...FONTS]
  return (
    <div className={`lm-text-layer ${compact ? 'compact' : ''}`}>
      {layer.approximate && (
        <div className="warn">
          <TriangleAlert size={12} /> {t('Approximate styling imported from Premiere: check font size and position.')}
        </div>
      )}
      <textarea
        value={text}
        rows={Math.min(4, Math.max(1, text.split('\n').length))}
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
      </div>
      <div className="row">
        <span className="k">{t('Font Size')}</span>
        <NumberScrub value={layer.font_size} min={1} max={2000} digits={0} onChange={(v) => set({ font_size: v }, t('Font Size'))} />
        <span style={{ flex: 1 }} />
        <button className={`lm-iconbtn ${layer.bold ? 'on' : ''}`} onClick={() => set({ bold: !layer.bold }, 'Bold')}>
          <Bold size={13} />
        </button>
        <button className={`lm-iconbtn ${layer.italic ? 'on' : ''}`} onClick={() => set({ italic: !layer.italic }, 'Italic')}>
          <Italic size={13} />
        </button>
        {(['left', 'center', 'right'] as const).map((a) => {
          const Icon = a === 'left' ? AlignLeft : a === 'center' ? AlignCenter : AlignRight
          return (
            <button key={a} className={`lm-iconbtn ${layer.align === a ? 'on' : ''}`} title={t('Alignment')} onClick={() => set({ align: a }, t('Alignment'))}>
              <Icon size={13} />
            </button>
          )
        })}
      </div>
      <div className="row">
        <label className="swatch">
          <input type="color" value={layer.color} onChange={(e) => set({ color: e.target.value }, t('Fill Color'))} />
          {t('Fill Color')}
        </label>
        <label className="swatch">
          <input
            type="checkbox"
            checked={!!layer.stroke_color && layer.stroke_width > 0}
            onChange={(e) => set(e.target.checked ? { stroke_color: layer.stroke_color ?? '#000000', stroke_width: layer.stroke_width || 4 } : { stroke_width: 0 }, t('Stroke'))}
          />
          <input type="color" value={layer.stroke_color ?? '#000000'} onChange={(e) => set({ stroke_color: e.target.value, stroke_width: layer.stroke_width || 4 }, t('Stroke'))} />
          {t('Stroke')}
        </label>
        <NumberScrub value={layer.stroke_width} min={0} max={200} digits={0} onChange={(v) => set({ stroke_width: v }, t('Stroke Width'))} />
        <label className="swatch">
          <input type="checkbox" checked={!!layer.background} onChange={(e) => set({ background: e.target.checked ? '#000000' : null }, t('Background'))} />
          <input type="color" value={layer.background ?? '#000000'} onChange={(e) => set({ background: e.target.value }, t('Background'))} />
          {t('Background')}
        </label>
      </div>
      {!compact && (
        <div className="row">
          <span className="k">{t('Position')}</span>
          <NumberScrub value={layer.position[0]} onChange={(v) => set({ position: [v, layer.position[1]] }, t('Position'))} />
          <NumberScrub value={layer.position[1]} onChange={(v) => set({ position: [layer.position[0], v] }, t('Position'))} />
          <span style={{ flex: 1 }} />
          {c.clip.graphic!.layers.length > 1 && (
            <button className="lm-iconbtn" title={t('Delete Layer')} onClick={() => void exec('graphic.remove_layer', { sequence_id: c.seq.id, clip_id: c.clip.id, layer_id: layer.id }, t('Delete Layer'))}>
              <Trash2 size={13} />
            </button>
          )}
        </div>
      )}
    </div>
  )
}
