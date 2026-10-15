"""Lumetri Color (Basic Correction and saturation), one definition for the export and the preview.

The preview runs the same maths in a WebGL shader (app/src/renderer/src/playback/GLCompositor.ts,
GRADE_FS); shared/test-vectors/lumetri.json pins both to the same numbers.

Per unpremultiplied pixel, in this order:
  white balance and exposure as gains in linear light (gamma 2.2),
  contrast around mid grey, highlights / shadows / whites / blacks as luma-weighted lifts,
  saturation scaled by vibrance (which favours unsaturated colours), then clamp.
"""

from __future__ import annotations

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
GAMMA = 2.2


def uniforms(p: dict) -> dict:
    """Slider values -> the shader's uniforms."""
    t, ti = p["temperature"] / 100, p["tint"] / 100
    e = 2.0 ** p["exposure"]
    return {
        "gain": (2.0 ** (0.3 * t) * e, 2.0 ** (-0.3 * ti) * e, 2.0 ** (-0.3 * t) * e),
        "contrast": 1 + p["contrast"] / 100,
        "tones": (0.25 * p["highlights"] / 100, 0.25 * p["shadows"] / 100, 0.2 * p["whites"] / 100, 0.2 * p["blacks"] / 100),
        "saturation": p["saturation"] / 100,
        "vibrance": p["vibrance"] / 100,
    }


def is_identity(p: dict) -> bool:
    return all(abs(p[k]) < 1e-9 for k in ("temperature", "tint", "exposure", "contrast", "highlights", "shadows", "whites", "blacks", "vibrance")) \
        and abs(p["saturation"] - 100) < 1e-9


def _smooth(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def grade(rgb: np.ndarray, p: dict) -> np.ndarray:
    """rgb float32 (..., 3) unpremultiplied in 0..1 -> graded, same shape."""
    u = uniforms(p)
    lin = np.power(np.clip(rgb, 0, 1), GAMMA) * np.asarray(u["gain"], dtype=np.float32)
    v = np.power(np.maximum(lin, 0), 1 / GAMMA)
    v = (v - 0.5) * u["contrast"] + 0.5
    lum = np.clip(v @ LUMA, 0, 1)[..., None]
    h, s, w, b = u["tones"]
    v = v + h * _smooth(0.5, 1, lum) + s * (1 - _smooth(0, 0.5, lum)) + w * lum ** 4 + b * (1 - lum) ** 4
    l2 = (v @ LUMA)[..., None]
    sat = np.clip(v.max(axis=-1, keepdims=True) - v.min(axis=-1, keepdims=True), 0, 1)
    f = u["saturation"] * (1 + u["vibrance"] * (1 - sat))
    return np.clip(l2 + (v - l2) * f, 0, 1).astype(np.float32)


def grade_image(img, p: dict):
    """A PIL RGBA / RGB image graded (alpha kept)."""
    from PIL import Image

    a = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255
    out = a.copy()
    out[..., :3] = grade(a[..., :3], p)
    return Image.fromarray(np.round(out * 255).astype(np.uint8), "RGBA")
