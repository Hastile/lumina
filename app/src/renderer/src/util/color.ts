// Lumetri Color: twin of engine/lumina/render/color.py (shared/test-vectors/lumetri.json).
// gradePixel is the reference; the preview runs the same maths in GLCompositor's GRADE_FS.

export type LumetriParams = {
  temperature: number
  tint: number
  exposure: number
  contrast: number
  highlights: number
  shadows: number
  whites: number
  blacks: number
  saturation: number
  vibrance: number
}

export const LUMETRI_KEYS: (keyof LumetriParams)[] = ['temperature', 'tint', 'exposure', 'contrast', 'highlights', 'shadows', 'whites', 'blacks', 'saturation', 'vibrance']

export type GradeUniforms = { gain: [number, number, number]; contrast: number; tones: [number, number, number, number]; saturation: number; vibrance: number }

const LUMA = [0.2126, 0.7152, 0.0722]
const GAMMA = 2.2

export function gradeUniforms(p: LumetriParams): GradeUniforms {
  const t = p.temperature / 100
  const ti = p.tint / 100
  const e = 2 ** p.exposure
  return {
    gain: [2 ** (0.3 * t) * e, 2 ** (-0.3 * ti) * e, 2 ** (-0.3 * t) * e],
    contrast: 1 + p.contrast / 100,
    tones: [(0.25 * p.highlights) / 100, (0.25 * p.shadows) / 100, (0.2 * p.whites) / 100, (0.2 * p.blacks) / 100],
    saturation: p.saturation / 100,
    vibrance: p.vibrance / 100
  }
}

export function isIdentity(p: LumetriParams): boolean {
  return LUMETRI_KEYS.every((k) => (k === 'saturation' ? Math.abs(p[k] - 100) < 1e-9 : Math.abs(p[k]) < 1e-9))
}

const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x))
const smooth = (e0: number, e1: number, x: number) => {
  const t = clamp((x - e0) / (e1 - e0))
  return t * t * (3 - 2 * t)
}
const dot = (v: number[]) => v[0] * LUMA[0] + v[1] * LUMA[1] + v[2] * LUMA[2]

/** One unpremultiplied pixel (0..1). */
export function gradePixel(rgb: number[], p: LumetriParams): number[] {
  const u = gradeUniforms(p)
  let v = rgb.map((c, i) => Math.max(0, clamp(c) ** GAMMA * u.gain[i]) ** (1 / GAMMA))
  v = v.map((c) => (c - 0.5) * u.contrast + 0.5)
  const lum = clamp(dot(v))
  const [h, s, w, b] = u.tones
  const lift = h * smooth(0.5, 1, lum) + s * (1 - smooth(0, 0.5, lum)) + w * lum ** 4 + b * (1 - lum) ** 4
  v = v.map((c) => c + lift)
  const l2 = dot(v)
  const sat = clamp(Math.max(...v) - Math.min(...v))
  const f = u.saturation * (1 + u.vibrance * (1 - sat))
  return v.map((c) => clamp(l2 + (c - l2) * f))
}

/** GLSL of gradePixel, on premultiplied texels. */
export const GRADE_GLSL = `
uniform vec3 gain;
uniform float contrastK;
uniform vec4 tones;
uniform float saturation;
uniform float vibrance;
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
vec3 gradeRgb(vec3 rgb) {
  vec3 v = pow(max(pow(clamp(rgb, 0.0, 1.0), vec3(2.2)) * gain, 0.0), vec3(1.0 / 2.2));
  v = (v - 0.5) * contrastK + 0.5;
  float lum = clamp(dot(v, LUMA), 0.0, 1.0);
  v += tones.x * smoothstep(0.5, 1.0, lum) + tones.y * (1.0 - smoothstep(0.0, 0.5, lum))
     + tones.z * pow(lum, 4.0) + tones.w * pow(1.0 - lum, 4.0);
  float l2 = dot(v, LUMA);
  float sat = clamp(max(max(v.r, v.g), v.b) - min(min(v.r, v.g), v.b), 0.0, 1.0);
  float f = saturation * (1.0 + vibrance * (1.0 - sat));
  return clamp(l2 + (v - l2) * f, 0.0, 1.0);
}`
