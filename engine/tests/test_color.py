import json
from pathlib import Path

import numpy as np
from PIL import Image

from lumina.effects import default_params
from lumina.render import color
from lumina.render.compositor import apply_effects

VECTORS = json.loads((Path(__file__).parents[2] / "shared" / "test-vectors" / "lumetri.json").read_text(encoding="utf-8"))


def test_grade_matches_the_shared_vectors():
    for c in VECTORS["cases"]:
        got = color.grade(np.array(c["in"], dtype=np.float32), c["params"])
        assert np.allclose(got, c["out"], atol=1e-5)


def test_neutral_settings_change_nothing_and_exposure_is_stops():
    p = default_params("lumetri", 100, 100)
    assert color.is_identity(p)
    px = np.array([[0.2, 0.5, 0.8]], dtype=np.float32)
    assert np.allclose(color.grade(px, p), px, atol=1e-6)
    lin = lambda v: v ** 2.2  # noqa: E731
    out = color.grade(px, {**p, "exposure": 1})
    assert np.allclose(lin(out[0, 0]), 2 * lin(0.2), atol=1e-4), "one stop doubles linear light"
    warm = color.grade(np.array([[0.5, 0.5, 0.5]], dtype=np.float32), {**p, "temperature": 60})[0]
    assert warm[0] > 0.5 > warm[2]
    grey = color.grade(np.array([[0.9, 0.2, 0.1]], dtype=np.float32), {**p, "saturation": 0})[0]
    assert np.ptp(grey) < 1e-6


def test_export_applies_lumetri_per_pixel():
    img = Image.new("RGBA", (4, 4), (128, 64, 32, 255))
    fx = {"id": "l", "type": "lumetri", "enabled": True, "params": {**default_params("lumetri", 4, 4), "exposure": 1, "saturation": 50}, "keyframes": {}}
    clip = {"source_in": 0, "start": 0, "speed": 1.0, "effects": [fx]}
    out, *_ = apply_effects(clip, img, (0.0, 0.0), (1.0, 1.0), 0, 4, 4)
    want = color.grade(np.array([[128, 64, 32]], dtype=np.float32) / 255, fx["params"])[0]
    assert np.allclose(np.asarray(out)[1, 1, :3] / 255, want, atol=1 / 255)
    assert np.asarray(out)[1, 1, 3] == 255
