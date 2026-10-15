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
  gain: number // Audio Gain x Volume (crossfades included), linear
  track: string
}

/** The Audio Track Mixer: per-track fader and pan, and the Mix fader (linear gains, pan -1..1). */
export type AudioMix = { tracks: { id: string; gain: number; pan: number }[]; master: number }

type Bus = { gain: GainNode; pan: StereoPannerNode; meter: Meter }
type Meter = { l: AnalyserNode; r: AnalyserNode }

const SCRUB = 0.09 // s of sound per scrub step (Premiere plays about a frame plus its tail)

type Live = { v: AudioVoice; gain: GainNode; nodes: Set<AudioBufferSourceNode>; stopped: boolean }

class AudioEngine {
  private ctx: AudioContext | null = null
  private session: { t0: number; c0: number; rate: number } | null = null
  private voices = new Map<string, Live>()
  private last = -Infinity
  private buses = new Map<string, Bus>()
  private master: { gain: GainNode; meter: Meter } | null = null
  private mixState: AudioMix = { tracks: [], master: 1 }
  private scrubbing: { at: number; nodes: AudioBufferSourceNode[] } | null = null
  /** Diagnostics for tests: buffers scheduled, and those that had to join late (an audible skip). */
  stats = { nodes: 0, late: 0, scrubs: 0 }

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

  private meter(ctx: AudioContext, from: AudioNode): Meter {
    const split = ctx.createChannelSplitter(2)
    from.connect(split)
    const mk = () => {
      const a = ctx.createAnalyser()
      a.fftSize = 2048
      return a
    }
    const m = { l: mk(), r: mk() }
    split.connect(m.l, 0)
    split.connect(m.r, 1)
    return m
  }

  /** Track bus: fader -> pan -> Mix fader -> output, metered after pan (like the mixer's meters). */
  private bus(id: string): Bus {
    const ctx = this.context()
    if (!this.master) {
      const gain = ctx.createGain()
      gain.gain.value = this.mixState.master
      gain.connect(ctx.destination)
      this.master = { gain, meter: this.meter(ctx, gain) }
    }
    let b = this.buses.get(id)
    if (!b) {
      const gain = ctx.createGain()
      const pan = ctx.createStereoPanner()
      const t = this.mixState.tracks.find((x) => x.id === id)
      gain.gain.value = t?.gain ?? 1
      pan.pan.value = t?.pan ?? 0
      gain.connect(pan)
      pan.connect(this.master.gain)
      b = { gain, pan, meter: this.meter(ctx, pan) }
      this.buses.set(id, b)
    }
    return b
  }

  /** Mixer state (track faders, pans, Mix fader); applied smoothly to the live buses. */
  mix(m: AudioMix): void {
    this.mixState = m
    const ctx = this.ctx
    if (!ctx) return
    for (const t of m.tracks) {
      const b = this.buses.get(t.id)
      if (!b) continue
      b.gain.gain.setTargetAtTime(t.gain, ctx.currentTime, 0.01)
      b.pan.pan.setTargetAtTime(t.pan, ctx.currentTime, 0.01)
    }
    this.master?.gain.gain.setTargetAtTime(m.master, ctx.currentTime, 0.01)
  }

  /** Peak levels (linear, 0..) of the last ~40 ms: the Mix and every track that has played. */
  levels(): { master: [number, number]; tracks: Record<string, [number, number]> } {
    const buf = new Float32Array(2048)
    const peak = (a: AnalyserNode) => {
      a.getFloatTimeDomainData(buf)
      let p = 0
      for (let i = 0; i < buf.length; i++) p = Math.max(p, Math.abs(buf[i]))
      return p
    }
    const tracks: Record<string, [number, number]> = {}
    for (const [id, b] of this.buses) tracks[id] = [peak(b.meter.l), peak(b.meter.r)]
    return { master: this.master ? [peak(this.master.meter.l), peak(this.master.meter.r)] : [0, 0], tracks }
  }

  /** Paused playhead moved (scrubbing, arrow keys): play a short slice of the mix there. */
  scrub(at: number, want: AudioVoice[]): void {
    if (this.session) return
    const ctx = this.context()
    const prev = this.scrubbing
    if (prev) for (const n of prev.nodes) n.stop(ctx.currentTime + 0.005)
    const cur = { at, nodes: [] as AudioBufferSourceNode[] }
    this.scrubbing = cur
    this.stats.scrubs++
    const T = TICKS_PER_SECOND
    for (const v of want) {
      if (at < v.span[0] || at >= v.span[1]) continue
      void openAudio(v.url).then(async (d) => {
        if (!d || this.scrubbing !== cur) return
        const src = v.sourceIn / T + ((at - v.start) / T) * v.speed
        const wb = await d.sink.getBuffer(src)
        if (!wb || this.scrubbing !== cur) return
        const n = ctx.createBufferSource()
        n.buffer = wb.buffer
        n.playbackRate.value = v.speed
        const env = ctx.createGain()
        const now = ctx.currentTime + 0.005
        env.gain.setValueAtTime(0, now)
        env.gain.linearRampToValueAtTime(v.gain, now + 0.005)
        env.gain.setValueAtTime(v.gain, now + SCRUB - 0.012)
        env.gain.linearRampToValueAtTime(0, now + SCRUB)
        n.connect(env)
        env.connect(this.bus(v.track).gain)
        const offset = Math.max(0, src - wb.timestamp)
        // a buffer is only ~20-40 ms long: chain the next one for the rest of the slice
        n.start(now, offset)
        cur.nodes.push(n)
        let end = wb.timestamp + wb.duration
        let when = now + (wb.duration - offset) / v.speed
        while (when < now + SCRUB) {
          const nb = await d.sink.getBuffer(end + 1e-4)
          if (!nb || nb.timestamp + nb.duration <= end || this.scrubbing !== cur) break
          const m = ctx.createBufferSource()
          m.buffer = nb.buffer
          m.playbackRate.value = v.speed
          m.connect(env)
          m.start(when, Math.max(0, end - nb.timestamp))
          cur.nodes.push(m)
          when += (nb.timestamp + nb.duration - end) / v.speed
          end = nb.timestamp + nb.duration
        }
        for (const x of cur.nodes) x.stop(now + SCRUB)
        setTimeout(() => env.disconnect(), (SCRUB + 0.2) * 1000)
      })
    }
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
      if (l && (l.v.track !== v.track || l.v.url !== v.url || l.v.start !== v.start || l.v.sourceIn !== v.sourceIn || l.v.speed !== v.speed || l.v.span[1] !== v.span[1])) {
        this.kill(l) // edited while playing
        l = undefined
      }
      if (!l) {
        const gain = ctx.createGain()
        gain.gain.value = v.gain
        gain.connect(this.bus(v.track).gain)
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
