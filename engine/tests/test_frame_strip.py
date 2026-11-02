from PIL import Image

from lumina import frame_strip


class FakeEngine:
    def __init__(self, tmp):
        self.tmp, self.calls = tmp, []

    def dispatch(self, method, params):
        self.calls.append((method, params))
        p = self.tmp / f"f{len(self.calls)}.png"
        Image.new("RGB", (1080, 1920), (40 * len(self.calls), 0, 0)).save(p)
        return {"path": str(p), "timecode": f"00:00:0{len(self.calls)}:00"}


def test_frames_are_tiled_in_order_and_cleaned_up(tmp_path):
    e = FakeEngine(tmp_path)
    data, legend = frame_strip.strip(e, {"sequence_id": "s", "times": ["0f", "30f", "2s"] * 3, "height": 320})
    assert [c[1]["time"] for c in e.calls][:3] == ["0f", "30f", "2s"]
    assert all(c[0] == "export.frame" and c[1]["sequence_id"] == "s" for c in e.calls)
    assert legend.startswith("1=00:00:01:00 2=00:00:02:00") and legend.count("=") == 9
    assert not list(tmp_path.glob("f*.png"))  # rendered frames are removed
    import io
    sheet = Image.open(io.BytesIO(data))
    assert sheet.height == 2 * 320 + 6  # 9 portrait tiles: two rows of up to eight
    assert sheet.width == 8 * 180 + 7 * 6
