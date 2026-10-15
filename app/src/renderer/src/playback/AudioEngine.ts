// Program monitor audio: every clip scheduled sample-accurately on one Web Audio
// clock (decoded by mediabunny), and that clock drives playback. This is how an
// editor keeps cuts clean: a clip's sound starts exactly at its cut, with no
// element warming up and no playback-rate nudging to chase the picture (which
// time-stretches the sound and makes cuts lurch). The picture follows the audio
// output, so they stay in sync without either one correcting the other.

import { ALL_FORMATS, AudioBufferSink, Input, UrlSource } from 'mediabunny'
import { TICKS_PER_SECOND } from '../util/time'

const LEAD = 0.06 // s between play() and the first scheduled sample (decoders start, nodes queue)
const AHEAD = 1.0 // s of audio scheduled in front of the output

type Decodable = { sink: AudioBufferSink }
const opened = new Map<string, Promise<Decodable | null>>()
const status = new Map<string, 'pending' | 'ok' | 'fail'>()

function openAudio(url: string): Promise<Decodable | null> {
  let p = opened.get(url)
  if (!p) {
    status.set(url, 'pending')
    p = (async () => {
      try {
        const input = new Input({ source: new UrlSource(url), formats: ALL_FORMATS })
        const track = await input.getPrimaryAudioTrack()
        if (!track || !(await track.canDecode())) return null
        return { sink: new AudioBufferSink(track) }
      } catch (err) {
        console.warn('[lumina] Web Audio path unavailable, using <audio>:', url, err)
        return null
      }
    })().then((d) => {
      status.set(url, d ? 'ok' : 'fail')
      return d
    })
    opened.set(url, p)
  }
  return p
}

/** What the Program monitor asks to hear: one clip, its sequence span (crossfade handles
 * included) and its gain now. Times are ticks. */
export type AudioVoice = {
  key: string
  url: string
  start: number // the clip's start (source_in maps here)
  sourceIn: number
  speed: number
  span: [number, number]
  gain: number
}

type Live = { v: AudioVoice; gain: GainNode; nodes: Set<AudioBufferSourceNode>; stopped: boolean }

class AudioEngine {
  private ctx: AudioContext | null = null
  private session: { t0: number; c0: number; rate: number } | null = null
  private voices = new Map<string, Live>()
  private last = -Infinity
  /** Diagnostics for tests: buffers scheduled, and those that had to join late (an audible skip). */
  stats = { nodes: 0, late: 0 }

  /** Whether a clip's media plays through Web Audio ('fail': use an <audio> element). */
  status(url: string): 'pending' | 'ok' | 'fail' {
    if (!status.has(url)) void openAudio(url)
    return status.get(url)!
  }

  private context(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext({ latencyHint: 'interactive' })
    if (this.ctx.state === 'suspended') void this.ctx.resume()
    return this.ctx
  }

  get running(): boolean {
    return !!this.session
  }

  /** Start the clock at sequence time ``t`` (ticks); voices are added by ``sync``. */
  start(t: number, rate: number): void {
    this.stop()
    if (rate <= 0) return // reverse play is silent, like before
    const ctx = this.context()
    this.session = { t0: t / TICKS_PER_SECOND, c0: ctx.currentTime + LEAD + (ctx.baseLatency || 0), rate }
    this.last = t
  }

  stop(): void {
    this.session = null
    for (const l of this.voices.values()) this.kill(l)
    this.voices.clear()
  }

  /** The sequence time (ticks) being heard at performance time ``now``: the playback clock. */
  clock(now: number): number | null {
    const s = this.session
    const ctx = this.ctx
    if (!s || !ctx) return null
    const ts = ctx.getOutputTimestamp()
    let heard: number
    if (ts.contextTime && ts.performanceTime) heard = ts.contextTime + (now - ts.performanceTime) / 1000
    else heard = ctx.currentTime - (ctx.outputLatency || 0)
    const t = (s.t0 + Math.max(0, heard - s.c0) * s.rate) * TICKS_PER_SECOND
    this.last = Math.max(this.last, t) // never step back on a timestamp update
    return this.last
  }

  /** Keep one scheduled voice per wanted clip; update gains. */
  sync(want: AudioVoice[]): void {
    const s = this.session
    if (!s) return
    const ctx = this.ctx!
    const keys = new Set(want.map((v) => v.key))
    for (const [k, l] of this.voices) {
      // a voice ends itself at its span end; one that left early (deleted, muted) stops now
      if (!keys.has(k)) {
        this.kill(l)
        this.voices.delete(k)
      }
    }
    for (const v of want) {
      let l = this.voices.get(v.key)
      if (l && (l.v.url !== v.url || l.v.start !== v.start || l.v.sourceIn !== v.sourceIn || l.v.speed !== v.speed || l.v.span[1] !== v.span[1])) {
        this.kill(l) // edited while playing
        l = undefined
      }
      if (!l) {
        const gain = ctx.createGain()
        gain.gain.value = v.gain
        gain.connect(ctx.destination)
        l = { v, gain, nodes: new Set(), stopped: false }
        this.voices.set(v.key, l)
        void this.schedule(l, s)
      } else if (Math.abs(l.v.gain - v.gain) > 1e-4) {
        // the picture shows the sequence time heard now; ramp from there
        l.gain.gain.setTargetAtTime(v.gain, ctx.currentTime, 0.004)
      }
      l.v = v
    }
  }

  private kill(l: Live): void {
    l.stopped = true
    for (const n of l.nodes) {
      try {
        n.stop()
      } catch {
        // not started
      }
    }
    l.nodes.clear()
    l.gain.disconnect()
  }

  private async schedule(l: Live, s: { t0: number; c0: number; rate: number }): Promise<void> {
    const d = await openAudio(l.v.url)
    const ctx = this.ctx!
    if (!d || l.stopped || this.session !== s) return
    const v = l.v
    const T = TICKS_PER_SECOND
    const toCtx = (seqSec: number) => s.c0 + (seqSec - s.t0) / s.rate
    const fromCtx = (c: number) => s.t0 + (c - s.c0) * s.rate
    const start = v.start / T
    const end = v.span[1] / T
    // the first sound: the span start, or what is about to be heard if we are already inside
    const from = Math.max(v.span[0] / T, s.t0, fromCtx(ctx.currentTime + 0.01))
    if (from >= end) return
    const src = (seqSec: number) => v.sourceIn / T + (seqSec - start) * v.speed
    const toSeq = (srcSec: number) => start + (srcSec - v.sourceIn / T) / v.speed
    try {
      for await (const wb of d.sink.buffers(src(from), src(end))) {
        if (l.stopped || this.session !== s) break
        const b0 = toSeq(wb.timestamp)
        const b1 = toSeq(wb.timestamp + wb.duration)
        if (b1 <= from) continue
        const seqAt = Math.max(b0, from)
        let when = toCtx(seqAt)
        let offset = (seqAt - b0) * v.speed
        const now = ctx.currentTime + 0.005
        if (when < now) {
          // decoding fell behind: join in at what is due now
          offset += (now - when) * s.rate * v.speed
          when = now
          this.stats.late++
          if (offset >= wb.duration) continue
        }
        const n = ctx.createBufferSource()
        n.buffer = wb.buffer
        n.playbackRate.value = v.speed * s.rate
        n.connect(l.gain)
        n.start(when, offset)
        if (b1 > end) n.stop(toCtx(end))
        l.nodes.add(n)
        this.stats.nodes++
        n.onended = () => l.nodes.delete(n)
        // backpressure: keep about AHEAD seconds queued
        while (!l.stopped && this.session === s && when - ctx.currentTime > AHEAD) await new Promise((r) => setTimeout(r, 100))
      }
    } catch (err) {
      if (!l.stopped) console.warn('[lumina] audio decode failed', v.url, err)
    }
  }
}

export const audioEngine = new AudioEngine()
