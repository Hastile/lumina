import { ArrowRightToLine, Hand, MousePointer2, MoveHorizontal, PenTool, Scissors, Type, ZoomIn, Columns2, ArrowLeftRight, Gauge, SplitSquareHorizontal } from 'lucide-react'
import { t } from '@shared/i18n'
import { runAction } from '../actions/actions'
import { useStore, type Tool } from '../state/store'

const TOOLS: { tool: Tool; title: string; key: string; Icon: React.FC<{ size?: number }> }[] = [
  { tool: 'select', title: 'Selection Tool', key: 'V', Icon: MousePointer2 },
  { tool: 'trackSelect', title: 'Track Select Forward Tool', key: 'A', Icon: ArrowRightToLine },
  { tool: 'ripple', title: 'Ripple Edit Tool', key: 'B', Icon: Columns2 },
  { tool: 'rolling', title: 'Rolling Edit Tool', key: 'N', Icon: SplitSquareHorizontal },
  { tool: 'rate', title: 'Rate Stretch Tool', key: 'R', Icon: Gauge },
  { tool: 'razor', title: 'Razor Tool', key: 'C', Icon: Scissors },
  { tool: 'slip', title: 'Slip Tool', key: 'Y', Icon: MoveHorizontal },
  { tool: 'slide', title: 'Slide Tool', key: 'U', Icon: ArrowLeftRight },
  { tool: 'pen', title: 'Pen Tool', key: 'P', Icon: PenTool },
  { tool: 'hand', title: 'Hand Tool', key: 'H', Icon: Hand },
  { tool: 'zoom', title: 'Zoom Tool', key: 'Z', Icon: ZoomIn },
  { tool: 'type', title: 'Type Tool', key: 'T', Icon: Type }
]

export function ToolsPanel() {
  const tool = useStore((s) => s.tool)
  return (
    <div className="lm-panel lm-tools">
      {TOOLS.map(({ tool: id, title, key, Icon }) => (
        <button key={id} className={`lm-iconbtn ${tool === id ? 'on' : ''}`} title={`${t(title)} (${key})`} onClick={() => runAction(`tool.${id}`)}>
          <Icon size={16} />
        </button>
      ))}
    </div>
  )
}
