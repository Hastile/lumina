// File > Export > Media (Ctrl+M) and the render-queue progress card.
// Laid out like Premiere's Export mode: settings on the left, the frame that
// will be rendered on the right, the big Export button bottom-right.
import { useEffect, useState } from 'react'
import { CheckCircle2, FolderOpen, Loader2, X, XCircle } from 'lucide-react'
import { create } from 'zustand'
import { t } from '@shared/i18n'
import { engine, mediaUrl, type Job } from '../engine/client'
import { activeSequence, call, get, sequenceEnd, toast, useStore } from '../state/store'
import { timecode } from '../util/time'

type Preset = { id: string; label: string; extension: string; has_video: boolean; hardware: boolean }

const useExport = create<{ open: boolean }>(() => ({ open: false }))
export const openExportDialog = () => useExport.setState({ open: true })

const sep = (p: string) => (p.includes('\\') ? '\\' : '/')
const dirOf = (p: string) => p.slice(0, Math.max(p.lastIndexOf('\\'), p.lastIndexOf('/')))
const safeName = (s: string) => s.replace(/[<>:"/\\|?*]+/g, '_').trim() || 'Sequence'

export function ExportDialog() {
  const open = useExport((s) => s.open)
  const doc = useStore((s) => s.doc)
  const projectPath = useStore((s) => s.path)
  const [presets, setPresets] = useState<Preset[]>([])
  const [preset, setPreset] = useState('h264')
  const [name, setName] = useState('')
  const [dir, setDir] = useState('')
  const [defaultDir, setDefaultDir] = useState('')
  const [range, setRange] = useState<'entire' | 'in_out'>('entire')
  const [hardware, setHardware] = useState(true)
  const [preview, setPreview] = useState<string | null>(null)
  const seq = doc ? activeSequence(get()) : null

  useEffect(() => {
    if (!open || !seq) return
    setName(safeName(seq.name))
    setRange(seq.in_point != null && seq.out_point != null ? 'in_out' : 'entire')
    void (async () => {
      setPresets((await call<Preset[]>('export.presets')) ?? [])
      const folder = await window.lumina.defaultExportDir(projectPath)
      setDir(folder)
      setDefaultDir(folder)
      const f = await call<{ path: string }>('export.frame', { sequence_id: seq.id, time: get().programTime, max_size: 640 })
      setPreview(f ? `${mediaUrl(f.path)}?v=${Date.now()}` : null)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null
  const close = () => useExport.setState({ open: false })
  if (!seq) {
    close()
    toast(t('No sequence is open'), 'info')
    return null
  }
  const st = seq.settings
  const p = presets.find((x) => x.id === preset)
  const ext = p?.extension ?? '.mp4'
  const path = `${dir}${dir ? sep(dir) : ''}${name}${ext}`
  const a = range === 'in_out' ? seq.in_point ?? 0 : 0
  const b = range === 'in_out' ? seq.out_point ?? sequenceEnd(seq) : sequenceEnd(seq)
  const fps = st.fps_num / st.fps_den

  const browse = async () => {
    const chosen = await window.lumina.saveDialog({
      title: t('Export'),
      defaultPath: path,
      filters: [{ name: p?.label ?? 'Media', extensions: [ext.slice(1)] }]
    })
    if (!chosen) return
    setDir(dirOf(chosen))
    setName(chosen.slice(dirOf(chosen).length + 1).replace(/\.[^.]+$/, ''))
  }
  const start = async () => {
    void window.lumina.setLastDir('export', dir)
    close()
    try {
      await engine.call('export.start', { sequence_id: seq.id, path, preset, range, hardware: hardware ? 'auto' : 'off' })
    } catch (err) {
      toast(`${t('Export failed')}: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return (
    <div className="lm-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <form
        className="lm-dialog lm-export"
        onSubmit={(e) => {
          e.preventDefault()
          void start()
        }}
        onKeyDown={(e) => e.key === 'Escape' && close()}
      >
        <h3>
          {t('Export')} · {seq.name}
        </h3>
        <div className="cols">
          <div className="body">
            <label htmlFor="ex-name">{t('File Name')}</label>
            <input id="ex-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            <label htmlFor="ex-dir">{t('Location')}</label>
            <span className="row">
              <input id="ex-dir" className="path" value={dir} title={dir} onChange={(e) => setDir(e.target.value)} />
              <button type="button" className="lm-btn small" onClick={() => void browse()}>
                {t('Browse...')}
              </button>
            </span>
            <label>기본 내보내기 폴더</label>
            <span className="row">
              <span className="dim" title={defaultDir} style={{ overflowWrap: 'anywhere' }}>{defaultDir}</span>
              <button type="button" className="lm-btn small" disabled={!dir.trim() || dir === defaultDir} onClick={() => {
                void window.lumina.setDefaultExportDir(dir).then(() => setDefaultDir(dir)).catch((err) => toast(String(err)))
              }}>현재 위치를 기본값으로</button>
            </span>
            <label htmlFor="ex-preset">{t('Format')}</label>
            <select id="ex-preset" value={preset} onChange={(e) => setPreset(e.target.value)}>
              {presets.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.label}
                </option>
              ))}
            </select>
            <label htmlFor="ex-range">{t('Range')}</label>
            <select id="ex-range" value={range} onChange={(e) => setRange(e.target.value as 'entire' | 'in_out')}>
              <option value="entire">{t('Entire Source')}</option>
              <option value="in_out" disabled={seq.in_point == null && seq.out_point == null}>
                {t('Source In/Out')}
              </option>
            </select>
            {p?.hardware && (
              <>
                <label htmlFor="ex-hw">{t('Hardware Encoding')}</label>
                <input id="ex-hw" type="checkbox" checked={hardware} onChange={(e) => setHardware(e.target.checked)} />
              </>
            )}
            <label>{t('Summary')}</label>
            <div className="summary">
              <div>{p?.has_video === false ? t('Audio only') : `${st.width} x ${st.height} · ${Number(fps.toFixed(3))} fps`}</div>
              <div>
                {t('Duration')} {timecode(Math.max(0, b - a), st)} · {st.sample_rate} Hz {t('Stereo')}
              </div>
              <div className="dim" title={path}>{path}</div>
            </div>
          </div>
          <div className="preview">{preview ? <img src={preview} alt="" /> : <div className="ph" />}</div>
        </div>
        <div className="actions">
          <button type="button" className="lm-btn" onClick={close}>
            {t('Cancel')}
          </button>
          <button type="submit" className="lm-btn primary" disabled={!name || !dir || b <= a}>
            {t('Export')}
          </button>
        </div>
      </form>
    </div>
  )
}

const PHASE: Record<string, string> = { queued: 'Queued', audio: 'Mixing audio', video: 'Rendering video', verify: 'Verifying' }

function fmtEta(s: number | null | undefined): string {
  if (s == null) return ''
  const m = Math.floor(s / 60)
  return `${m}:${String(Math.round(s % 60)).padStart(2, '0')} ${t('remaining')}`
}

export function RenderQueue() {
  const jobs = useStore((s) => s.jobs)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const exports = Object.values(jobs).filter((j): j is Job => j.kind === 'export' && !dismissed.has(j.id))
  if (!exports.length) return null
  const dismiss = (id: string) => setDismissed(new Set([...dismissed, id]))
  return (
    <div className="lm-renderq">
      {exports.map((j) => {
        const file = (j.path ?? '').split(/[\\/]/).pop()
        const active = j.status === 'queued' || j.status === 'running'
        return (
          <div key={j.id} className={`card ${j.status}`} data-job={j.id}>
            <div className="head">
              {active ? <Loader2 size={14} className="spin" /> : j.status === 'done' ? <CheckCircle2 size={14} color="#5fbf6f" /> : <XCircle size={14} color="#e5534b" />}
              <span className="name" title={j.path ?? ''}>{file}</span>
              <span style={{ flex: 1 }} />
              {active ? (
                <button className="lm-iconbtn" title={t('Cancel')} onClick={() => void call('export.cancel', { job_id: j.id })}>
                  <X size={13} />
                </button>
              ) : (
                <button className="lm-iconbtn" title={t('Close')} onClick={() => dismiss(j.id)}>
                  <X size={13} />
                </button>
              )}
            </div>
            {active && (
              <>
                <div className="bar">
                  <div style={{ width: `${Math.round((j.progress ?? 0) * 100)}%` }} />
                </div>
                <div className="meta">
                  <span>{t(PHASE[j.phase ?? 'queued'] ?? 'Exporting')} {Math.round((j.progress ?? 0) * 100)}%</span>
                  <span>{j.fps ? `${j.fps} fps · ${fmtEta(j.eta_seconds)}` : ''}</span>
                </div>
              </>
            )}
            {j.status === 'done' && (
              <div className="meta">
                <span>
                  {t('Export complete')} · {j.result ? `${j.result.elapsed_seconds}s · ${(j.result.size_bytes / 1e6).toFixed(1)} MB · ${j.result.encoder ?? 'pcm'}` : ''}
                </span>
                <button className="lm-btn small" onClick={() => void window.lumina.showItemInFolder(j.path ?? '')}>
                  <FolderOpen size={12} /> {t('Show in Explorer')}
                </button>
              </div>
            )}
            {j.status === 'failed' && <div className="meta error">{t('Export failed')}: {j.error}</div>}
            {j.status === 'cancelled' && <div className="meta">{t('Export cancelled')}</div>}
          </div>
        )
      })}
    </div>
  )
}

/** File > Export > Frame (Ctrl+Shift+E): the Program monitor frame at the playhead. */
export async function exportFrame(): Promise<void> {
  const seq = activeSequence(get())
  if (!seq) return toast(t('No sequence is open'), 'info')
  const tc = timecode(get().programTime, seq.settings).replace(/[:;]/g, '')
  const dir = await window.lumina.defaultExportDir(get().path)
  const target = await window.lumina.saveDialog({
    title: t('Export Frame...'),
    defaultPath: `${dir}${sep(dir)}${safeName(seq.name)}_${tc}.png`,
    filters: [
      { name: 'PNG', extensions: ['png'] },
      { name: 'JPEG', extensions: ['jpg'] }
    ]
  })
  if (!target) return
  const r = await call<{ path: string }>('export.frame', { sequence_id: seq.id, time: get().programTime, path: target })
  if (r) toast(`${t('Frame exported')}: ${r.path}`, 'info')
}
