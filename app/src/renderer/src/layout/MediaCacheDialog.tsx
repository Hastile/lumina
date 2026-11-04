// Edit > Media Cache (Premiere's Preferences > Media Cache): where generated thumbnails, waveforms
// and proxies live, how much space they take, deleting them, and deleting old ones automatically.
import { useEffect, useState } from 'react'
import { create } from 'zustand'
import { t } from '@shared/i18n'
import { def } from '../actions/actions'
import { askChoice } from '../state/dialog'
import { call, toast } from '../state/store'

type CacheInfo = {
  root: string
  default_root: string
  cache_dir: string | null
  env_override: boolean
  auto_clean_days: number
  items: number
  bytes: number
  used_items: number
  used_bytes: number
}

const useCacheDialog = create<{ open: boolean }>(() => ({ open: false }))

export function openMediaCacheDialog(): void {
  useCacheDialog.setState({ open: true })
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`
}

export function MediaCacheDialog() {
  const open = useCacheDialog((s) => s.open)
  const [info, setInfo] = useState<CacheInfo | null>(null)
  const [days, setDays] = useState(30)
  const [busy, setBusy] = useState(false)

  const refresh = async () => {
    const i = await call<CacheInfo>('cache.info')
    if (!i) return
    setInfo(i)
    if (i.auto_clean_days) setDays(i.auto_clean_days)
  }
  useEffect(() => {
    if (open) void refresh()
    else setInfo(null)
  }, [open])
  if (!open) return null
  const close = () => useCacheDialog.setState({ open: false })

  const settings = async (p: Record<string, unknown>) => {
    if ((await call('cache.settings', p)) !== null) await refresh()
  }
  const browse = async () => {
    const [dir] = await window.lumina.openDialog({ title: t('Media Cache Location'), properties: ['openDirectory', 'createDirectory'], defaultPath: info?.root })
    if (dir) await settings({ cache_dir: dir })
  }
  const clean = async (mode: 'unused' | 'all') => {
    if (mode === 'all') {
      const ok = await askChoice({
        title: t('Delete All Media Cache Files'),
        message: t('Thumbnails, waveforms and proxies of open projects are generated again afterwards.'),
        buttons: [{ label: t('Delete'), value: 'ok', primary: true }, { label: t('Cancel'), value: 'cancel' }],
        cancel: 'cancel'
      })
      if (ok !== 'ok') return
    }
    setBusy(true)
    const r = await call<{ removed: number; freed_bytes: number; failed: string[] }>('cache.clean', { mode })
    setBusy(false)
    if (r) toast(`${t('Deleted media cache')}: ${r.removed} ${t('items')}, ${formatBytes(r.freed_bytes)}${r.failed.length ? ` (${r.failed.length} ${t('failed')})` : ''}`, 'info')
    await refresh()
  }

  return (
    <div className="lm-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="lm-dialog lm-cache" data-media-cache="" tabIndex={-1} onKeyDown={(e) => e.key === 'Escape' && close()}>
        <h3>{t('Media Cache')}</h3>
        <div className="body">
          <label>{t('Location')}</label>
          <span className="pair">
            <span className="path" title={info?.root} data-cache-root="">{info?.root ?? '…'}</span>
            <button type="button" className="lm-btn" disabled={!info || info.env_override} onClick={() => void browse()}>
              {t('Browse...')}
            </button>
            {info?.cache_dir && (
              <button type="button" className="lm-btn" onClick={() => void settings({ cache_dir: null })}>
                {t('Default')}
              </button>
            )}
          </span>
          {info?.env_override && <div className="note">{t('Set by LUMINA_CACHE.')}</div>}
          <label>{t('Size')}</label>
          <span data-cache-size="">
            {info ? `${formatBytes(info.bytes)} · ${info.items} ${t('media')} (${t('open projects')}: ${formatBytes(info.used_bytes)})` : '…'}
          </span>
          <label>{t('Remove Media Cache Files')}</label>
          <span className="pair">
            <button type="button" className="lm-btn" disabled={busy || !info} data-cache-clean="unused" onClick={() => void clean('unused')}>
              {t('Delete Unused')}
            </button>
            <button type="button" className="lm-btn" disabled={busy || !info} data-cache-clean="all" onClick={() => void clean('all')}>
              {t('Delete All')}
            </button>
          </span>
          <div className="note">{t('Unused: media that no open project uses.')}</div>
          <label>{t('Media Cache Management')}</label>
          <span className="pair">
            <input
              type="checkbox"
              checked={!!info?.auto_clean_days}
              data-cache-auto=""
              onChange={(e) => void settings({ auto_clean_days: e.target.checked ? days : 0 })}
            />
            {t('Automatically delete cache files older than')}
            <input
              type="number" onFocus={(e) => e.currentTarget.select()}
              min={1}
              max={3650}
              value={days}
              style={{ width: 60 }}
              onChange={(e) => setDays(Number(e.target.value))}
              onBlur={() => info?.auto_clean_days && days > 0 && void settings({ auto_clean_days: days })}
            />
            {t('days')}
          </span>
        </div>
        <div className="actions">
          <button type="button" className="lm-btn primary" onClick={close}>
            {t('Close')}
          </button>
        </div>
      </div>
    </div>
  )
}

def('edit.mediaCache', t('Media Cache...'), openMediaCacheDialog)
