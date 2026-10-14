import { t } from '@shared/i18n'
import { exec } from '../state/store'
import { openDialog } from '../state/dialog'
import { parseTimecode, timecode, type Rate } from '../util/time'

/** Premiere: right-click a transition > Set Transition Duration... */
export function transitionDurationDialog(sequenceId: string, transitionId: string, current: number, rate: Rate): void {
  openDialog({
    title: t('Set Transition Duration...'),
    okLabel: 'OK',
    fields: [{ key: 'duration', label: t('Duration'), type: 'text', value: timecode(current, rate) }],
    onSubmit: async (v) => {
      const ticks = parseTimecode(String(v.duration), rate)
      if (ticks == null || ticks <= 0) return
      await exec('transition.set', { sequence_id: sequenceId, transition_id: transitionId, duration: ticks }, t('Set Transition Duration...'))
    }
  })
}
