// Frame-exact video for the monitors: WebCodecs decoding (via mediabunny's
// demuxers) into a small cache of decoded frames, driven by the playback clock.
//
// The monitors ask a FrameSource for "the frame at source time t" on every
// clock tick; what they get is exactly that frame when it is decoded, or the
// nearest decoded one while decoding catches up (never a frame of some other
// clip, never a blank). Three access patterns:
//   - forward play: one sequential decode stream kept AHEAD seconds in front of
//     the clock (restarted only on a jump);
//   - paused / scrubbing: random access, latest request wins (no backlog while
//     dragging the playhead);
//   - reverse play (J): whole GOPs decoded into the cache, the previous GOP
//     prefetched before the clock reaches it.

import { ALL_FORMATS, EncodedPacketSink, Input, UrlSource, VideoSampleSink, type InputVideoTrack, type VideoSample } from 'mediabunny'

/** A decoded frame copied out of the decoder. Hardware decoders own only ~10 output frames and
 * stall while those stay open, so frames are copied to GPU bitmaps and released immediately. */
export type Frame = { timestamp: number; duration: number; bitmap: ImageBitmap }

async function copyFrame(s: VideoSample, scale = 1): Promise<Frame | null> {
  try {
    const vf = s.toVideoFrame()
    try {
      const opts: ImageBitmapOptions | undefined = scale < 1
        ? { resizeWidth: Math.max(2, Math.round(vf.displayWidth * scale)), resizeHeight: Math.max(2, Math.round(vf.displayHeight * scale)), resizeQuality: 'medium' }
        : undefined
      return { timestamp: s.timestamp, duration: s.duration, bitmap: await createImageBitmap(vf, opts) }
    } finally {
      vf.close()
    }
  } catch {
    return null
  } finally {
    s.close()
  }
}

export type DecodableMedia = {
  url: string
  track: InputVideoTrack
  packets: EncodedPacketSink
  width: number
  height: number
  frameDuration: number // seconds (from the first packet; used for "is t inside this frame")
}

const opened = new Map<string, Promise<DecodableMedia | null>>()

/** Demux a media URL once; null when WebCodecs cannot decode its video (callers fall back to <video>). */
export function openMedia(url: string): Promise<DecodableMedia | null> {
  let p = opened.get(url)
  if (!p) {
    p = (async () => {
      try {
        const input = new Input({ source: new UrlSource(url), formats: ALL_FORMATS })
        const track = await input.getPrimaryVideoTrack()
        if (!track || !(await track.canDecode())) return null
        const packets = new EncodedPacketSink(track)
        const first = await packets.getFirstPacket({ metadataOnly: true })
        const second = first ? await packets.getNextPacket(first, { metadataOnly: true }) : null
        const frameDuration = first?.duration || (second && first ? second.timestamp - first.timestamp : 0) || 1 / 30
        return { url, track, packets, width: track.displayWidth, height: track.displayHeight, frameDuration }
      } catch (err) {
        console.warn('[lumina] WebCodecs path unavailable, using <video>:', url, err)
        return null
      }
    })()
    opened.set(url, p)
  }
  return p
}

const AHEAD = 0.6 // seconds decoded in front of the clock during forward play
const KEEP_BEHIND = 0.25 // seconds kept behind the clock (forward)
const REVERSE_CHUNK = 0.5 // seconds per reverse-play chunk (decoded from its keyframe, only its frames kept)
const MAX_FRAMES = 72
const PRIME_DELAY = 150 // ms the paused playhead must rest before a forward stream is primed

export class FrameSource {
  readonly media: DecodableMedia
  /** Called whenever a newly decoded frame may change what should be shown. */
  onFrame: () => void = () => undefined
  private sink: VideoSampleSink
  private cache: Frame[] = [] // sorted by timestamp
  private want = 0
  private playing = false
  private rate = 1
  private stream: AsyncGenerator<VideoSample, void, unknown> | null = null
  private streamFrom = 0
  private lastPulled = -Infinity
  private wake: (() => void) | null = null
  private seekBusy = false
  private seekNext: number | null = null
  private chunkBusy = new Set<number>()
  private chunkDone = new Set<number>()
  /** Premiere's Playback Resolution: frames decoded while playing are kept at this scale (paused = full). */
  playbackScale = 0.5
  private primeTimer: ReturnType<typeof setTimeout> | null = null
  private disposed = false

  constructor(media: DecodableMedia) {
    this.media = media
    this.sink = new VideoSampleSink(media.track)
  }

  /** Diagnostics for tests: seconds decoded ahead of the clock, cached frames, stream state. */
  get debug(): string {
    const c = this.cache
    return `${(this.lastPulled - this.want).toFixed(2)}|${c.length}|${c.length ? c[0].timestamp.toFixed(2) + '-' + c[c.length - 1].timestamp.toFixed(2) : ''}|${this.stream ? 's' : '-'}`
  }

  /** Frame showing at ``t`` if decoded, else the closest decoded one (or null before anything is ready). */
  frame(t: number): { sample: Frame; exact: boolean } | null {
    const c = this.cache
    if (!c.length) return null
    let lo = 0
    let hi = c.length - 1
    let best = -1
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (c[mid].timestamp <= t + 1e-6) {
        best = mid
        lo = mid + 1
      } else hi = mid - 1
    }
    if (best >= 0) {
      const s = c[best]
      const dur = s.duration || this.media.frameDuration
      // exact when t falls inside this frame and no gap to a later one is missing
      const exact = t < s.timestamp + dur + 1e-6 || (best + 1 < c.length && c[best + 1].timestamp > t)
      return { sample: s, exact }
    }
    return { sample: c[0], exact: false }
  }

  /** Drive decoding toward source time ``t`` (seconds). Cheap; call on every clock tick. */
  update(t: number, playing: boolean, rate: number): void {
    if (this.disposed) return
    this.want = t
    this.playing = playing
    this.rate = rate
    if (playing && rate > 0) {
      // keep one stream while the clock moves forward inside what it has decoded; a jump restarts it.
      // A clip about to start is driven here with t frozen at its first frame, so its stream is
      // already running (decoder warm, AHEAD frames ready) when the cut arrives.
      const live = this.stream && t >= this.streamFrom - 1e-6 && t <= this.lastPulled + 1.5
      if (!live) this.startStream(t)
    } else if (playing && rate < 0) {
      this.stopStream()
      this.reverse(t)
    } else {
      // paused: show the frame now, and once the playhead rests, prime a forward stream so
      // pressing play continues from already-decoded frames instead of warming up the decoder
      const live = this.stream && t >= this.streamFrom - 1e-6 && t <= this.lastPulled + 1.5
      if (!live) this.stopStream()
      if (!this.frame(t)?.exact) this.seek(t)
      if (this.primeTimer) clearTimeout(this.primeTimer)
      if (!live) {
        this.primeTimer = setTimeout(() => {
          this.primeTimer = null
          if (!this.disposed && !this.playing && this.want === t && !this.stream) this.startStream(t)
        }, PRIME_DELAY)
      }
    }
    this.wake?.()
    this.evict()
  }

  dispose(): void {
    this.disposed = true
    if (this.primeTimer) clearTimeout(this.primeTimer)
    this.stopStream()
    for (const f of this.cache) f.bitmap.close()
    this.cache = []
  }

  // ---- forward play: one sequential decode stream with backpressure

  private startStream(t: number): void {
    this.stopStream()
    const gen = this.sink.samples(t)
    this.stream = gen
    this.streamFrom = t
    this.lastPulled = t - 1e-6
    void this.pump(gen)
  }

  private stopStream(): void {
    const gen = this.stream
    this.stream = null
    this.wake?.()
    if (gen) void gen.return(undefined).catch(() => undefined)
  }

  private async pump(gen: AsyncGenerator<VideoSample, void, unknown>): Promise<void> {
    try {
      for await (const raw of gen) {
        if (gen !== this.stream || this.disposed) {
          raw.close()
          break
        }
        const s = await copyFrame(raw, this.playbackScale)
        if (!s) continue
        if (gen !== this.stream || this.disposed) {
          s.bitmap.close()
          break
        }
        this.insert(s)
        this.lastPulled = s.timestamp
        this.onFrame()
        while (gen === this.stream && !this.disposed && s.timestamp > this.want + AHEAD) {
          await new Promise<void>((r) => (this.wake = r))
          this.wake = null
        }
      }
    } catch (err) {
      if (gen === this.stream) console.warn('[lumina] decode stream failed', err)
    }
  }

  // ---- paused / scrubbing: random access, latest request wins

  private seek(t: number): void {
    if (this.seekBusy) {
      this.seekNext = t
      return
    }
    this.seekBusy = true
    void (async () => {
      let target: number | null = t
      while (target !== null && !this.disposed) {
        this.seekNext = null
        if (!this.frame(target)?.exact) {
          try {
            const raw = await this.sink.getSample(target)
            const s = raw ? await copyFrame(raw) : null
            if (s && !this.disposed) {
              this.insert(s)
              this.onFrame()
            } else s?.bitmap.close()
          } catch (err) {
            console.warn('[lumina] seek decode failed', err)
          }
        }
        target = this.seekNext
      }
      this.seekBusy = false
    })()
  }

  // ---- reverse play: decode whole GOPs, prefetch the previous one

  private reverse(t: number): void {
    // fixed chunks [kW, (k+1)W): the current one, and the previous one once we are inside it.
    // Long-GOP sources (YouTube: 3-6 s) would need hundreds of frames per GOP; a chunk re-decodes
    // from its keyframe but keeps only its own frames (decoding runs far above real time)
    const k = Math.floor(t / REVERSE_CHUNK)
    void this.decodeChunk(k).then(() => this.decodeChunk(k - 1))
  }

  private async decodeChunk(k: number): Promise<void> {
    if (k < 0 || this.chunkBusy.has(k) || this.chunkDone.has(k) || this.disposed) return
    const a = k * REVERSE_CHUNK
    const b = a + REVERSE_CHUNK
    this.chunkBusy.add(k)
    try {
      const key = await this.media.packets.getKeyPacket(a, { metadataOnly: true })
      for await (const raw of this.sink.samples(key ? key.timestamp : 0, b)) {
        if (this.disposed) {
          raw.close()
          break
        }
        if (raw.timestamp + (raw.duration || this.media.frameDuration) <= a + 1e-6) {
          raw.close() // before the chunk: decoded only to reach it
          continue
        }
        const s = await copyFrame(raw, this.playbackScale)
        if (s) this.insert(s)
      }
      this.chunkDone.add(k)
      this.onFrame()
    } catch (err) {
      console.warn('[lumina] reverse chunk decode failed', err)
    } finally {
      this.chunkBusy.delete(k)
    }
  }

  // ---- cache

  private insert(s: Frame): void {
    if (this.disposed) {
      s.bitmap.close()
      return
    }
    const c = this.cache
    let i = c.length
    while (i > 0 && c[i - 1].timestamp > s.timestamp) i--
    if (i > 0 && Math.abs(c[i - 1].timestamp - s.timestamp) < 1e-6) {
      s.bitmap.close()
      return
    }
    c.splice(i, 0, s)
    this.evict()
  }

  private evict(): void {
    const c = this.cache
    const reverse = this.playing && this.rate < 0
    const lo = this.want - (reverse ? 2.2 * REVERSE_CHUNK : KEEP_BEHIND)
    const hi = this.want + (reverse ? 0.25 : AHEAD * 2)
    const showing = this.frame(this.want)?.sample
    for (let i = c.length - 1; i >= 0; i--) {
      const s = c[i]
      if (s !== showing && (s.timestamp < lo || s.timestamp > hi)) {
        s.bitmap.close()
        c.splice(i, 1)
        this.chunkDone.delete(Math.floor(s.timestamp / REVERSE_CHUNK))
      }
    }
    while (c.length > MAX_FRAMES) {
      // drop whichever end is farther from the clock
      const s = Math.abs(c[0].timestamp - this.want) > Math.abs(c[c.length - 1].timestamp - this.want) ? c.shift()! : c.pop()!
      if (s === showing) {
        c.push(s)
        c.sort((a, b) => a.timestamp - b.timestamp)
        break
      }
      s.bitmap.close()
      this.chunkDone.delete(Math.floor(s.timestamp / REVERSE_CHUNK))
    }
  }
}
