import { useMemo, useState } from 'react'
import { t } from '@shared/i18n'
import { ChevronLeft, Film, Folder, FolderPlus, Image, LayoutGrid, List, Music, Search, Clapperboard, FilePlus2, Captions, Layers, Sparkles } from 'lucide-react'
import type { Asset, MediaItem, Sequence } from '../engine/document'
import { mediaUrl } from '../engine/client'
import { importFiles, runAction } from '../actions/actions'
import { openContextMenu } from '../layout/Chrome'
import { focusPanel } from '../layout/dock'
import { exec, get, openSequence, set, useStore } from '../state/store'
import { linkMedia, makeOffline, newAdjustmentLayer, placeAsset } from '../actions/extra'
import { timecode } from '../util/time'

export const DRAG_MIME = 'application/x-lumina-item'

type Row =
  | { kind: 'bin'; id: string; name: string }
  | { kind: 'media'; id: string; name: string; item: MediaItem }
  | { kind: 'sequence'; id: string; name: string; seq: Sequence }
  | { kind: 'asset'; id: string; name: string; asset: Asset }

function rateLabel(num?: number, den?: number): string {
  if (!num || !den) return ''
  const v = num / den
  return `${Number.isInteger(v) ? v.toFixed(2) : v.toFixed(3)} fps`
}

function durationLabel(r: Row): string {
  if (r.kind === 'media') {
    const v = r.item.video
    const rate = v ? { fps_num: v.fps_num, fps_den: v.fps_den } : { fps_num: 30, fps_den: 1 }
    return r.item.kind === 'image' ? '' : timecode(r.item.duration, rate)
  }
  if (r.kind === 'asset') {
    const cues = r.asset.cues ?? []
    return cues.length ? timecode(cues[cues.length - 1].end, { fps_num: 30, fps_den: 1 }) : ''
  }
  if (r.kind === 'sequence') {
    let end = 0
    for (const t of Object.values(r.seq.tracks)) for (const c of Object.values(t.clips)) end = Math.max(end, c.end)
    return timecode(end, r.seq.settings)
  }
  return ''
}

function openRow(r: Row): void {
  if (r.kind === 'bin') set({ currentBin: r.id, selectedItems: [] })
  else if (r.kind === 'asset') void placeAsset(r.id)
  else if (r.kind === 'media') {
    set({ sourceMediaId: r.id, sourceTime: r.item.in_point ?? 0 })
    focusPanel('source')
  } else {
    openSequence(r.id)
    set({ programTime: r.seq.playhead })
    focusPanel('timeline')
  }
}

function RowIcon({ r }: { r: Row }) {
  if (r.kind === 'asset') {
    const A = r.asset.kind === 'captions' ? Captions : r.asset.kind === 'template' ? Sparkles : Layers
    return <A size={14} color={`var(--label-${r.asset.label})`} style={{ flex: 'none' }} />
  }
  const color = r.kind === 'media' ? `var(--label-${r.item.label})` : r.kind === 'sequence' ? 'var(--label-forest)' : '#c9a64a'
  const Icon = r.kind === 'bin' ? Folder : r.kind === 'sequence' ? Clapperboard : r.item.kind === 'audio' ? Music : r.item.kind === 'image' ? Image : Film
  return <Icon size={14} color={color} style={{ flex: 'none' }} />
}

export function ProjectPanel() {
  const doc = useStore((s) => s.doc)
  const currentBin = useStore((s) => s.currentBin)
  const selected = useStore((s) => s.selectedItems)
  const jobs = useStore((s) => s.jobs)
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'list' | 'icon'>('list')
  const [dropping, setDropping] = useState(false)

  const binId = currentBin && doc?.bins[currentBin] ? currentBin : doc?.root_bin_id
  const rows = useMemo<Row[]>(() => {
    if (!doc || !binId) return []
    const q = query.trim().toLowerCase()
    const inBin = (b: string) => (q ? true : b === binId)
    const out: Row[] = [
      ...Object.values(doc.bins).filter((b) => b.parent_id && inBin(b.parent_id)).map((b) => ({ kind: 'bin' as const, id: b.id, name: b.name })),
      ...Object.values(doc.sequences).filter((s) => inBin(s.bin_id)).map((s) => ({ kind: 'sequence' as const, id: s.id, name: s.name, seq: s })),
      ...Object.values(doc.media).filter((m) => inBin(m.bin_id)).map((m) => ({ kind: 'media' as const, id: m.id, name: m.name, item: m })),
      ...Object.values(doc.assets ?? {}).filter((a) => inBin(a.bin_id)).map((a) => ({ kind: 'asset' as const, id: a.id, name: a.name, asset: a }))
    ]
    return out.filter((r) => !q || r.name.toLowerCase().includes(q)).sort((a, b) => a.name.localeCompare(b.name))
  }, [doc, binId, query])

  if (!doc) return <div className="lm-panel" />
  const bin = doc.bins[binId!]
  const busy = new Set(Object.values(jobs).filter((j) => j.status === 'running' || j.status === 'queued').map((j) => j.media_id))

  const select = (e: React.MouseEvent, id: string) => {
    const s = get().selectedItems
    if (e.ctrlKey) set({ selectedItems: s.includes(id) ? s.filter((x) => x !== id) : [...s, id] })
    else if (e.shiftKey && s.length) {
      const ids = rows.map((r) => r.id)
      const a = ids.indexOf(s[s.length - 1])
      const b = ids.indexOf(id)
      set({ selectedItems: ids.slice(Math.min(a, b), Math.max(a, b) + 1) })
    } else if (!s.includes(id)) set({ selectedItems: [id] })
  }

  const onDragStart = (e: React.DragEvent, r: Row) => {
    const ids = selected.includes(r.id) ? selected : [r.id]
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(ids))
    e.dataTransfer.effectAllowed = 'copyMove'
  }

  const onDrop = async (e: React.DragEvent, targetBin?: string) => {
    e.preventDefault()
    setDropping(false)
    const internal = e.dataTransfer.getData(DRAG_MIME)
    if (internal) {
      const ids = (JSON.parse(internal) as string[]).filter((id) => id !== targetBin)
      if (targetBin && ids.length) await exec('bin.move_items', { item_ids: ids, bin_id: targetBin })
      return
    }
    const paths = [...e.dataTransfer.files].map((f) => window.lumina.pathForFile(f)).filter(Boolean)
    if (paths.length) await importFiles(paths)
  }

  const rowMenu = (e: React.MouseEvent, r?: Row) => {
    if (r && !selected.includes(r.id)) set({ selectedItems: [r.id] })
    openContextMenu(e, [
      { label: t('New Bin'), action: 'file.newBin' },
      { label: t('New Sequence...'), action: 'file.newSequence' },
      { label: t('New Item > Adjustment Layer'), run: () => void newAdjustmentLayer() },
      'sep',
      { label: t('Import...'), action: 'file.import' },
      'sep',
      { label: t('Rename'), action: 'clip.rename', disabled: !r },
      { label: t('Clear'), action: 'edit.clear', disabled: !r },
      'sep',
      {
        label: t('New Sequence From Clip'),
        disabled: !r || r.kind !== 'media',
        run: () => r && r.kind === 'media' && void exec('sequence.create_from_media', { media_id: r.id }).then(() => focusPanel('timeline'))
      },
      { label: t('Duplicate'), disabled: !r || r.kind !== 'sequence', run: () => r && void exec('sequence.duplicate', { sequence_id: r.id }) },
      ...(r && r.kind === 'asset' ? [{ label: r.asset.kind === 'captions' ? t('Place on a Caption Track') : t('Place at Playhead'), run: () => void placeAsset(r.id) }] : []),
      'sep',
      { label: t('Link Media...'), disabled: !r || r.kind !== 'media', run: () => r && r.kind === 'media' && linkMedia(r.id) },
      {
        label: r && r.kind === 'media' && r.item.offline ? t('Make Online') : t('Make Offline'),
        disabled: !r || r.kind !== 'media',
        run: () => {
          const ids = get().selectedItems.filter((id) => get().doc?.media[id])
          const offline = !(r && r.kind === 'media' && r.item.offline)
          void makeOffline(ids, offline)
        }
      },
      { label: t('Search for Missing Media...'), action: 'project.locateMedia' },
      { label: t('Scan Media (Offline / Duplicates)...'), action: 'project.scanMedia' }
    ])
  }

  return (
    <div
      className={`lm-panel lm-project ${dropping ? 'dropping' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        if (e.dataTransfer.types.includes('Files')) setDropping(true)
      }}
      onDragLeave={() => setDropping(false)}
      onDrop={(e) => onDrop(e)}
      onContextMenu={(e) => rowMenu(e)}
    >
      <div className="lm-toolbar lm-project-head">
        {bin?.parent_id ? (
          <button className="lm-iconbtn" title={t('Up one level')} onClick={() => set({ currentBin: bin.parent_id })}>
            <ChevronLeft size={15} />
          </button>
        ) : null}
        <span className="lm-project-title">{bin?.name ?? doc.name}.lumina</span>
        <span className="lm-count">{rows.length} {t('items')}</span>
      </div>
      <div className="lm-toolbar">
        <div className="lm-search">
          <Search size={13} />
          <input placeholder="" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </div>
      {view === 'list' ? (
        <div className="lm-project-list" onMouseDown={(e) => e.target === e.currentTarget && set({ selectedItems: [] })}>
          <div className="lm-project-row header">
            <span className="name">{t('Name')}</span>
            <span>{t('Frame Rate')}</span>
            <span>{t('Media Duration')}</span>
            <span>{t('Video Info')}</span>
          </div>
          {rows.map((r) => (
            <div
              key={r.id}
              className={`lm-project-row ${selected.includes(r.id) ? 'selected' : ''}`}
              draggable
              onDragStart={(e) => onDragStart(e, r)}
              onDragOver={(e) => r.kind === 'bin' && e.preventDefault()}
              onDrop={(e) => {
                if (r.kind === 'bin') {
                  e.stopPropagation()
                  void onDrop(e, r.id)
                }
              }}
              onMouseDown={(e) => select(e, r.id)}
              onDoubleClick={() => openRow(r)}
              onContextMenu={(e) => rowMenu(e, r)}
            >
              <span className="name">
                <span className="swatch" style={{ background: r.kind === 'media' ? `var(--label-${r.item.label})` : r.kind === 'sequence' ? 'var(--label-forest)' : 'transparent' }} />
                <RowIcon r={r} />
                <span className="text">{r.name}</span>
                {r.kind === 'media' && busy.has(r.id) && <span className="busy">{t('processing')}</span>}
                {r.kind === 'media' && r.item.offline && <span className="busy offline">{t('offline')}</span>}
              </span>
              <span>{r.kind === 'media' ? rateLabel(r.item.video?.fps_num, r.item.video?.fps_den) : r.kind === 'sequence' ? rateLabel(r.seq.settings.fps_num, r.seq.settings.fps_den) : ''}</span>
              <span>{durationLabel(r)}</span>
              <span>
                {r.kind === 'media' && r.item.video ? `${r.item.video.width} x ${r.item.video.height}` : r.kind === 'sequence' ? `${r.seq.settings.width} x ${r.seq.settings.height}` : ''}
              </span>
            </div>
          ))}
          {!rows.length && <div className="lm-empty">{t('Import media to start')}<br />{t('(drag files here, or Ctrl+I)')}</div>}
        </div>
      ) : (
        <div className="lm-project-grid" onMouseDown={(e) => e.target === e.currentTarget && set({ selectedItems: [] })}>
          {rows.map((r) => (
            <div
              key={r.id}
              className={`lm-tile ${selected.includes(r.id) ? 'selected' : ''}`}
              draggable
              onDragStart={(e) => onDragStart(e, r)}
              onMouseDown={(e) => select(e, r.id)}
              onDoubleClick={() => openRow(r)}
              onContextMenu={(e) => rowMenu(e, r)}
            >
              <div className="thumb">
                {r.kind === 'media' && r.item.thumbnail_path ? <img src={mediaUrl(r.item.thumbnail_path)} /> : <RowIcon r={r} />}
                <span className="dur">{durationLabel(r)}</span>
              </div>
              <div className="cap">
                <RowIcon r={r} />
                <span className="text">{r.name}</span>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="lm-toolbar lm-project-foot">
        <button className={`lm-iconbtn ${view === 'list' ? 'on' : ''}`} title={t('List View')} onClick={() => setView('list')}>
          <List size={15} />
        </button>
        <button className={`lm-iconbtn ${view === 'icon' ? 'on' : ''}`} title={t('Icon View')} onClick={() => setView('icon')}>
          <LayoutGrid size={15} />
        </button>
        <span style={{ flex: 1 }} />
        <button className="lm-iconbtn" title={t('New Bin')} onClick={() => runAction('file.newBin')}>
          <FolderPlus size={15} />
        </button>
        <button className="lm-iconbtn" title={t('New Item')} onClick={(e) => openContextMenu(e, [{ label: t('Sequence...'), action: 'file.newSequence' }, { label: t('Adjustment Layer'), run: () => void newAdjustmentLayer() }, { label: t('Import...'), action: 'file.import' }])}>
          <FilePlus2 size={15} />
        </button>
      </div>
    </div>
  )
}

