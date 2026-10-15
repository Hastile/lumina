// Effects panel: Premiere's folder tree of presets, effects and transitions.
// Drag an effect onto a clip (timeline or Effect Controls) or a transition onto
// an edit point; double-click applies to the selection / the playhead.
import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Download, Folder, Search, Sparkles, SquareSplitHorizontal, Wand2 } from 'lucide-react'
import { t } from '@shared/i18n'
import { engine } from '../engine/client'
import { activeSequence, get, toast, useStore } from '../state/store'
import { EFFECTS, TRANSITIONS } from '../util/effects'

export const EFFECT_MIME = 'application/x-lumina-effect'
export type EffectDrag = { kind: 'effect' | 'transition' | 'preset'; type: string }
type Preset = { id: string; name: string; folder: string[] }

/** Presets folder tree from the library (engine presets.list). */
function presetNodes(presets: Preset[]): (Node | Leaf)[] {
  const root: Node = { label: 'Presets', children: [] }
  for (const p of [...presets].sort((a, b) => [...a.folder, a.name].join('/').localeCompare([...b.folder, b.name].join('/')))) {
    let node = root
    for (const f of p.folder) {
      let next = node.children.find((c): c is Node => !isLeaf(c) && c.label === f)
      if (!next) {
        next = { label: f, children: [] }
        node.children.push(next)
      }
      node = next
    }
    node.children.push({ id: `preset:${p.id}`, label: p.name, drag: { kind: 'preset', type: p.id } })
  }
  return root.children
}

export async function importPremierePresets(): Promise<void> {
  try {
    const r = await engine.call<{ imported: number; skipped: string[]; path: string }>('presets.import_premiere', {})
    toast(`${t('Imported Premiere presets')}: ${r.imported}${r.skipped.length ? ` (${t('skipped')} ${r.skipped.length})` : ''}`, 'info')
    window.dispatchEvent(new Event('lumina:presets'))
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err))
  }
}

type Leaf = { id: string; label: string; drag: EffectDrag }
type Node = { label: string; children: (Node | Leaf)[] }

const group = (kind: 'effect' | 'transition', entries: [string, { label: string; category: string }][]): Node[] => {
  const cats = new Map<string, Leaf[]>()
  for (const [type, d] of entries) {
    if (!cats.has(d.category)) cats.set(d.category, [])
    cats.get(d.category)!.push({ id: `${kind}:${type}`, label: d.label, drag: { kind, type } })
  }
  return [...cats].map(([label, children]) => ({ label, children }))
}

const BUILTIN: Node[] = [
  { label: 'Lumetri Presets', children: [] },
  { label: 'Audio Effects', children: [] },
  { label: 'Audio Transitions', children: group('transition', Object.entries(TRANSITIONS).filter(([, d]) => d.kind === 'audio')) },
  { label: 'Video Effects', children: group('effect', Object.entries(EFFECTS)) },
  { label: 'Video Transitions', children: group('transition', Object.entries(TRANSITIONS).filter(([, d]) => d.kind === 'video')) }
]

function isLeaf(n: Node | Leaf): n is Leaf {
  return 'drag' in n
}

/** Double-click: effects go on the selected video clips, transitions on the edit point nearest the playhead. */
export async function applyFromPanel(drag: EffectDrag): Promise<void> {
  const s = get()
  const seq = activeSequence(s)
  if (!seq) return toast(t('No sequence is open'), 'info')
  try {
    if (drag.kind === 'preset') {
      const ids = s.selectedClips.filter((id) => seq.video_tracks.some((tid) => seq.tracks[tid].clips[id]))
      if (!ids.length) return toast(t('Select a clip first'), 'info')
      await engine.exec('effect.apply_preset', { sequence_id: seq.id, clip_ids: ids, preset_id: drag.type }, t('Apply Preset'))
      return
    }
    if (drag.kind === 'effect') {
      const ids = s.selectedClips.filter((id) => seq.video_tracks.some((tid) => seq.tracks[tid].clips[id]))
      if (!ids.length) return toast(t('Select a clip first'), 'info')
      await engine.exec('effect.add', { sequence_id: seq.id, clip_ids: ids, type: drag.type }, t('Apply Effect'))
      return
    }
    const kind = TRANSITIONS[drag.type as keyof typeof TRANSITIONS].kind
    await engine.call('transaction.begin', { label: t('Apply Transition') })
    try {
      const sel = s.selectedClips.length ? s.selectedClips : null
      const r = await engine.exec<{ transition_ids: string[] }>('transition.apply_default', { sequence_id: seq.id, kind, clip_ids: sel })
      for (const id of r.transition_ids) await engine.exec('transition.set', { sequence_id: seq.id, transition_id: id, type: drag.type })
      await engine.call('transaction.commit')
    } catch (err) {
      await engine.call('transaction.rollback').catch(() => undefined)
      throw err
    }
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err))
  }
}

export function EffectsPanel() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set(['Presets', 'Video Effects', 'Video Transitions']))
  const [presets, setPresets] = useState<Preset[]>([])
  const refresh = useCallback(() => void engine.call<Preset[]>('presets.list', {}).then(setPresets).catch(() => undefined), [])
  const connected = useStore((s) => s.connected)
  useEffect(() => {
    if (connected) refresh()
    window.addEventListener('lumina:presets', refresh)
    return () => window.removeEventListener('lumina:presets', refresh)
  }, [refresh, connected])
  const TREE: Node[] = [{ label: 'Presets', children: presetNodes(presets) }, ...BUILTIN]
  useStore((s) => s.selectedClips) // re-render for selection-dependent hints
  const query = q.trim().toLowerCase()
  const matches = (l: Leaf) => !query || t(l.label).toLowerCase().includes(query) || l.label.toLowerCase().includes(query)

  const render = (n: Node | Leaf, depth: number, path: string): React.ReactNode => {
    if (isLeaf(n)) {
      if (!matches(n)) return null
      const Icon = n.drag.kind === 'effect' ? Sparkles : n.drag.kind === 'preset' ? Wand2 : SquareSplitHorizontal
      return (
        <div
          key={n.id}
          className="node leaf"
          data-effect-item={n.id}
          style={{ paddingLeft: 8 + depth * 14 }}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData(EFFECT_MIME, JSON.stringify(n.drag))
            e.dataTransfer.effectAllowed = 'copy'
          }}
          onDoubleClick={() => void applyFromPanel(n.drag)}
        >
          <Icon size={12} color="#8fb4ff" /> {t(n.label)}
        </div>
      )
    }
    const key = `${path}/${n.label}`
    const kids = n.children.map((c) => render(c, depth + 1, key)).filter(Boolean)
    if (query && !kids.length) return null
    const isOpen = !!query || open.has(n.label) || open.has(key)
    return (
      <div key={key}>
        <div
          className="node"
          style={{ paddingLeft: 4 + depth * 14 }}
          onClick={() => {
            const next = new Set(open)
            if (next.has(n.label) || next.has(key)) {
              next.delete(n.label)
              next.delete(key)
            } else next.add(key)
            setOpen(next)
          }}
        >
          {n.children.length ? isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} /> : <span style={{ width: 12, display: 'inline-block' }} />}
          <Folder size={13} color="#c9a64a" /> {t(n.label)}
        </div>
        {isOpen && kids}
      </div>
    )
  }

  return (
    <div className="lm-panel">
      <div className="lm-toolbar">
        <div className="lm-search">
          <Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="" />
        </div>
        <button className="lm-iconbtn" title={t('Import Premiere Presets')} data-import-presets onClick={() => void importPremierePresets()}>
          <Download size={13} />
        </button>
      </div>
      <div className="lm-tree lm-fxtree">{TREE.map((n) => render(n, 0, ''))}</div>
    </div>
  )
}
