// WebGL2 compositor for the Program monitor. Mirrors the export renderer
// (engine/lumina/render/compositor.py): per layer, the standard effects run in
// source space (Transform as an affine, Gaussian Blur materialized with a margin
// in premultiplied alpha), then crop, then Motion into sequence pixels; layers
// composite bottom to top with premultiplied "over"; a transition's parts are
// summed in their own buffer first (cross dissolve = premultiplied mix).
//
// Conventions: all textures hold premultiplied colour. Render targets are drawn
// y-down, so their top row sits at v = 1; uploaded ImageBitmaps keep their top
// row at v = 0 (WebGL ignores UNPACK_FLIP_Y / PREMULTIPLY for ImageBitmap
// sources) and are marked ``flip`` so the shader reads them the same way.

import { GRADE_GLSL, type GradeUniforms } from '../util/color'

export type Affine = [number, number, number, number, number, number] // [a b c / d e f]
export type Blend = 'normal' | 'add' | 'multiply'

/** A texture placed in some 2D space: it covers [x, x + w] x [y, y + h] of that space. */
export type Placed = { tex: WebGLTexture; x: number; y: number; w: number; h: number; flip?: boolean }

export type Target = { fbo: WebGLFramebuffer | null; width: number; height: number }

const VS = `#version 300 es
in vec2 unit;
uniform vec4 rect;       // placed texture rect in its space
uniform mat3 toTarget;   // its space -> target pixels
uniform mat3 toCrop;     // its space -> crop space
uniform vec2 targetSize;
uniform float flip;      // 1 when the texture's top row is at v = 0 (uploaded bitmaps)
out vec2 vUv;
out vec2 vCrop;
void main() {
  vec2 s = rect.xy + unit * rect.zw;
  vec2 p = (toTarget * vec3(s, 1.0)).xy;
  vUv = vec2(unit.x, flip > 0.5 ? unit.y : 1.0 - unit.y);
  vCrop = (toCrop * vec3(s, 1.0)).xy;
  gl_Position = vec4(p.x / targetSize.x * 2.0 - 1.0, 1.0 - p.y / targetSize.y * 2.0, 0.0, 1.0);
}`

const FS = `#version 300 es
precision highp float;
in vec2 vUv;
in vec2 vCrop;
uniform sampler2D tex;
uniform vec4 tint;       // premultiplied multiplier (opacity, solid colours)
uniform vec4 crop;       // x0 y0 x1 y1 in crop space; x0 > x1 disables
out vec4 color;
void main() {
  if (crop.x <= crop.z && (vCrop.x < crop.x || vCrop.y < crop.y || vCrop.x > crop.z || vCrop.y > crop.w)) discard;
  color = texture(tex, vUv) * tint;
}`

const BLUR_FS = `#version 300 es
precision highp float;
in vec2 vUv;
in vec2 vCrop;
uniform sampler2D tex;
uniform vec2 texel;      // one texel step along the blur direction (uv units)
uniform float sigma;
uniform int radius;
out vec4 color;
void main() {
  float wsum = 0.0;
  vec4 acc = vec4(0.0);
  for (int i = -128; i <= 128; i++) {
    if (i < -radius || i > radius) continue;
    float w = exp(-0.5 * float(i * i) / (sigma * sigma));
    acc += texture(tex, vUv + texel * float(i)) * w;
    wsum += w;
  }
  color = acc / wsum;
}`

// Lumetri Color on a premultiplied buffer (util/color.ts GRADE_GLSL)
const GRADE_FS = `#version 300 es
precision highp float;
in vec2 vUv;
in vec2 vCrop;
uniform sampler2D tex;
out vec4 color;
${GRADE_GLSL}
void main() {
  vec4 c = texture(tex, vUv);
  if (c.a <= 0.0) { color = vec4(0.0); return; }
  color = vec4(gradeRgb(c.rgb / c.a) * c.a, c.a);
}`

function compile(gl: WebGL2RenderingContext, vs: string, fs: string): WebGLProgram {
  const sh = (type: number, src: string) => {
    const s = gl.createShader(type)!
    gl.shaderSource(s, src)
    gl.compileShader(s)
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader')
    return s
  }
  const p = gl.createProgram()!
  gl.attachShader(p, sh(gl.VERTEX_SHADER, vs))
  gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs))
  gl.bindAttribLocation(p, 0, 'unit')
  gl.linkProgram(p)
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? 'link')
  return p
}

const mat3 = (m: Affine) => new Float32Array([m[0], m[3], 0, m[1], m[4], 0, m[2], m[5], 1]) // column-major

export const IDENTITY: Affine = [1, 0, 0, 0, 1, 0]

export function mul(m: Affine, n: Affine): Affine {
  const [a, b, c, d, e, f] = m
  const [A, B, C, D, E, F] = n
  return [a * A + b * D, a * B + b * E, a * C + b * F + c, d * A + e * D, d * B + e * E, d * C + e * F + f]
}

type RT = { fbo: WebGLFramebuffer; tex: WebGLTexture; width: number; height: number }

export class GLCompositor {
  readonly gl: WebGL2RenderingContext
  private prog: WebGLProgram
  private blurProg: WebGLProgram
  private gradeProg: WebGLProgram
  private quad: WebGLVertexArrayObject
  private white: WebGLTexture
  private main: RT | null = null
  private group: RT | null = null
  private scratch: RT[] = []
  /** sequence pixels -> canvas pixels */
  private k = 1
  private seq = { w: 1, h: 1 }

  constructor(readonly canvas: HTMLCanvasElement) {
    const gl = canvas.getContext('webgl2', { premultipliedAlpha: true, alpha: false, antialias: false, preserveDrawingBuffer: true })
    if (!gl) throw new Error('WebGL2 unavailable')
    this.gl = gl
    this.prog = compile(gl, VS, FS)
    this.blurProg = compile(gl, VS, BLUR_FS)
    this.gradeProg = compile(gl, VS, GRADE_FS)
    this.quad = gl.createVertexArray()!
    gl.bindVertexArray(this.quad)
    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
    this.white = this.texture()
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([255, 255, 255, 255]))
  }

  texture(): WebGLTexture {
    const gl = this.gl
    const t = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, t)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    return t
  }

  /** Upload an ImageBitmap as is (create it with premultiplyAlpha: 'premultiply'; place it with flip: true). */
  upload(tex: WebGLTexture, src: ImageBitmap): void {
    const gl = this.gl
    gl.bindTexture(gl.TEXTURE_2D, tex)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src)
  }

  private rt(width: number, height: number, reuse?: RT | null): RT {
    const gl = this.gl
    width = Math.max(1, Math.round(width))
    height = Math.max(1, Math.round(height))
    if (reuse && reuse.width === width && reuse.height === height) return reuse
    if (reuse) this.free(reuse)
    const tex = this.texture()
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null)
    const fbo = gl.createFramebuffer()!
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo)
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0)
    return { fbo, tex, width, height }
  }

  private free(r: RT): void {
    this.gl.deleteFramebuffer(r.fbo)
    this.gl.deleteTexture(r.tex)
  }

  /** Size the output: ``scale`` canvas pixels per sequence pixel. */
  resize(seqW: number, seqH: number, scale: number): void {
    const w = Math.max(1, Math.round(seqW * scale))
    const h = Math.max(1, Math.round(seqH * scale))
    if (this.canvas.width !== w) this.canvas.width = w
    if (this.canvas.height !== h) this.canvas.height = h
    this.k = w / seqW
    this.seq = { w: seqW, h: seqH }
    this.main = this.rt(w, h, this.main)
    this.group = this.rt(w, h, this.group)
  }

  private bind(t: Target): void {
    const gl = this.gl
    gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo)
    gl.viewport(0, 0, t.width, t.height)
  }

  private clear(t: Target): void {
    const gl = this.gl
    this.bind(t)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
  }

  private blend(b: Blend): void {
    const gl = this.gl
    gl.enable(gl.BLEND)
    if (b === 'add') gl.blendFunc(gl.ONE, gl.ONE)
    else if (b === 'multiply') gl.blendFuncSeparate(gl.DST_COLOR, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    else gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
  }

  /** Draw a placed texture through ``toTarget`` (its space -> target pixels). */
  draw(t: Target, p: Placed, toTarget: Affine, opts: { tint?: [number, number, number, number]; blend?: Blend; toCrop?: Affine; crop?: [number, number, number, number] | null } = {}): void {
    const gl = this.gl
    this.bind(t)
    gl.useProgram(this.prog)
    gl.bindVertexArray(this.quad)
    this.blend(opts.blend ?? 'normal')
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, p.tex)
    const u = (n: string) => gl.getUniformLocation(this.prog, n)
    gl.uniform1i(u('tex'), 0)
    gl.uniform4f(u('rect'), p.x, p.y, p.w, p.h)
    gl.uniformMatrix3fv(u('toTarget'), false, mat3(toTarget))
    gl.uniformMatrix3fv(u('toCrop'), false, mat3(opts.toCrop ?? IDENTITY))
    gl.uniform2f(u('targetSize'), t.width, t.height)
    gl.uniform1f(u('flip'), p.flip ? 1 : 0)
    gl.uniform4fv(u('tint'), opts.tint ?? [1, 1, 1, 1])
    gl.uniform4fv(u('crop'), opts.crop ?? [1, 0, 0, 0])
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }

  // ---- frame structure

  /** The composited frame in sequence pixels (draw targets for layers). */
  get frameTarget(): Target {
    return { fbo: this.main!.fbo, width: this.main!.width, height: this.main!.height }
  }

  get groupTarget(): Target {
    return { fbo: this.group!.fbo, width: this.group!.width, height: this.group!.height }
  }

  /** sequence pixels -> frame pixels */
  get seqToFrame(): Affine {
    return [this.k, 0, 0, 0, this.k, 0]
  }

  begin(): void {
    this.clear(this.frameTarget)
  }

  beginGroup(): void {
    this.clear(this.groupTarget)
  }

  /** Composite the transition group buffer over the frame. */
  endGroup(): void {
    const g = this.group!
    this.draw(this.frameTarget, { tex: g.tex, x: 0, y: 0, w: this.seq.w, h: this.seq.h }, this.seqToFrame)
  }

  solid(t: Target, rgba: [number, number, number, number], blend: Blend = 'normal'): void {
    const a = rgba[3]
    this.draw(t, { tex: this.white, x: 0, y: 0, w: this.seq.w, h: this.seq.h }, this.seqToFrame, { tint: [rgba[0] * a, rgba[1] * a, rgba[2] * a, a], blend })
  }

  /** Present the frame over black. */
  end(): void {
    const gl = this.gl
    const m = this.main!
    this.bind({ fbo: null, width: this.canvas.width, height: this.canvas.height })
    gl.disable(gl.BLEND)
    gl.clearColor(0, 0, 0, 1)
    gl.clear(gl.COLOR_BUFFER_BIT)
    this.draw({ fbo: null, width: this.canvas.width, height: this.canvas.height }, { tex: m.tex, x: 0, y: 0, w: this.seq.w, h: this.seq.h }, this.seqToFrame)
    for (const r of this.scratch) this.free(r)
    this.scratch = []
  }

  // ---- effects in source space

  /** Render ``p`` through ``pre`` (source -> source) at ``ps`` pixels per source pixel into a
   * new buffer padded by ``pad`` pixels; returns it placed in source space. */
  materialize(p: Placed, pre: Affine, opacity: number, srcW: number, srcH: number, ps: number, pad: number): Placed {
    const r = this.rt(srcW * ps + 2 * pad, srcH * ps + 2 * pad)
    this.scratch.push(r)
    const t = { fbo: r.fbo, width: r.width, height: r.height }
    this.clear(t)
    this.draw(t, p, mul([ps, 0, pad, 0, ps, pad], pre), { tint: [opacity, opacity, opacity, opacity] })
    return { tex: r.tex, x: -pad / ps, y: -pad / ps, w: r.width / ps, h: r.height / ps }
  }

  /** Separable Gaussian blur of a placed buffer (sigma in its texels). */
  blur(p: Placed, texW: number, texH: number, sx: number, sy: number): Placed {
    const gl = this.gl
    let cur = p
    for (const [dx, dy, s] of [[1, 0, sx], [0, 1, sy]] as const) {
      if (s < 0.05) continue
      const r = this.rt(texW, texH)
      this.scratch.push(r)
      const t = { fbo: r.fbo, width: r.width, height: r.height }
      this.clear(t)
      this.bind(t)
      gl.useProgram(this.blurProg)
      gl.bindVertexArray(this.quad)
      gl.disable(gl.BLEND)
      gl.activeTexture(gl.TEXTURE0)
      gl.bindTexture(gl.TEXTURE_2D, cur.tex)
      const u = (n: string) => gl.getUniformLocation(this.blurProg, n)
      gl.uniform1i(u('tex'), 0)
      gl.uniform4f(u('rect'), 0, 0, texW, texH)
      gl.uniformMatrix3fv(u('toTarget'), false, mat3(IDENTITY))
      gl.uniformMatrix3fv(u('toCrop'), false, mat3(IDENTITY))
      gl.uniform2f(u('targetSize'), texW, texH)
      gl.uniform1f(u('flip'), cur.flip ? 1 : 0)
      gl.uniform2f(u('texel'), dx / texW, (cur.flip ? dy : -dy) / texH)
      gl.uniform1f(u('sigma'), s)
      gl.uniform1i(u('radius'), Math.min(128, Math.ceil(3 * s)))
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
      cur = { tex: r.tex, x: cur.x, y: cur.y, w: cur.w, h: cur.h, flip: false }
    }
    return cur
  }

  /** Lumetri Color of a placed buffer, texel for texel. */
  grade(p: Placed, texW: number, texH: number, g: GradeUniforms): Placed {
    const gl = this.gl
    const r = this.rt(texW, texH)
    this.scratch.push(r)
    const t = { fbo: r.fbo, width: r.width, height: r.height }
    this.bind(t)
    gl.useProgram(this.gradeProg)
    gl.bindVertexArray(this.quad)
    gl.disable(gl.BLEND)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, p.tex)
    const u = (n: string) => gl.getUniformLocation(this.gradeProg, n)
    gl.uniform1i(u('tex'), 0)
    gl.uniform4f(u('rect'), 0, 0, r.width, r.height)
    gl.uniformMatrix3fv(u('toTarget'), false, mat3(IDENTITY))
    gl.uniformMatrix3fv(u('toCrop'), false, mat3(IDENTITY))
    gl.uniform2f(u('targetSize'), r.width, r.height)
    gl.uniform1f(u('flip'), p.flip ? 1 : 0)
    gl.uniform3f(u('gain'), ...g.gain)
    gl.uniform1f(u('contrastK'), g.contrast)
    gl.uniform4f(u('tones'), ...g.tones)
    gl.uniform1f(u('saturation'), g.saturation)
    gl.uniform1f(u('vibrance'), g.vibrance)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
    return { tex: r.tex, x: p.x, y: p.y, w: p.w, h: p.h, flip: false }
  }

  /** Read the presented frame (tests). */
  readPixels(): { width: number; height: number; data: Uint8Array } {
    const gl = this.gl
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    const data = new Uint8Array(this.canvas.width * this.canvas.height * 4)
    gl.readPixels(0, 0, this.canvas.width, this.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, data)
    return { width: this.canvas.width, height: this.canvas.height, data }
  }

  dispose(): void {
    for (const r of [this.main, this.group, ...this.scratch]) if (r) this.free(r)
    this.gl.getExtension('WEBGL_lose_context')?.loseContext()
  }
}
