import { createRoot } from 'react-dom/client'
import 'dockview-react/dist/styles/dockview.css'
import './styles/theme.css'
import './styles/panels.css'
import { App } from './App'
import { engine } from './engine/client'
import { useStore } from './state/store'

// test/automation hook (used by scripts/e2e.mjs)
;(window as unknown as Record<string, unknown>).__lumina = { get: useStore.getState, engine }

createRoot(document.getElementById('root')!).render(<App />)
