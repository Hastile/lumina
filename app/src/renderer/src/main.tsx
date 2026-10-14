import { createRoot } from 'react-dom/client'
import 'dockview-react/dist/styles/dockview.css'
import './styles/theme.css'
import './styles/panels.css'
import { setLocale, type Locale } from '@shared/i18n'
import { App } from './App'
import { engine } from './engine/client'
import { useStore } from './state/store'
import { play, stop } from './state/transport'

// test/automation hook (used by scripts/e2e.mjs)
;(window as unknown as Record<string, unknown>).__lumina = { get: useStore.getState, set: useStore.setState, engine, play, stop }

let locale: Locale = 'ko'
try {
  locale = (localStorage.getItem('lumina.locale') as Locale | null) ?? 'ko'
} catch {
  /* storage unavailable: keep Korean */
}
setLocale(locale)
void window.lumina.setLocale(locale)

createRoot(document.getElementById('root')!).render(<App />)
