import { describe, expect, it } from 'vitest'
import { sequenceTextRows, textCursorOffset } from '../src/renderer/src/util/textRows'
import type { Sequence } from '../src/renderer/src/engine/document'

describe('Text panel rows', () => {
  it('reads captions from every track and keeps graphics separate in timeline order', () => {
    const seq = { video_tracks: ['v', 'c1', 'c2'], tracks: {
      v: { name: 'Title', locked: false, clips: { g: { id: 'g', start: 0, graphic: { layers: [{ id: 'l', type: 'text', text: '제목' }, { id: 'shape', type: 'shape' }] } } } },
      c1: { name: 'Pop', locked: false, clips: { a: { id: 'a', start: 10, caption: { text: '앞' } } } },
      c2: { name: 'Slam', locked: true, clips: { b: { id: 'b', start: 20, caption: { text: '뒤' } } } },
    } } as unknown as Sequence
    const rows = sequenceTextRows(seq)
    expect(rows.map(r => [r.kind, r.text, r.trackLabel])).toEqual([['graphics', '제목', 'V1'], ['captions', '앞', 'V2'], ['captions', '뒤', 'V3']])
    expect(rows[2].locked).toBe(true)
  })
  it('maps textarea UTF-16 positions correctly for emoji and Korean', () => {
    expect(textCursorOffset('안녕🎬 다음', 5)).toBe(4)
  })
})
