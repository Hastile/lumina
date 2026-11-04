from lumina.media.jobs import has_alpha, make_proxy, stale_proxy
import shutil
import subprocess
import pytest


def test_alpha_proxy_keeps_dimensions_and_transparency(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('lumina.media.jobs._run', lambda cmd, **kw: calls.append(cmd))
    item = {'kind': 'video', 'path': 'title.mov', 'video': {'pix_fmt': 'yuva444p12le'}, 'audio': None}
    output = make_proxy(item, tmp_path)
    cmd = calls[0]
    assert output.suffix == '.webm'
    assert cmd[cmd.index('-c:v') + 1] == 'libvpx-vp9'
    assert cmd[cmd.index('-lossless') + 1] == '1'
    assert cmd[cmd.index('-pix_fmt') + 1] == 'yuva420p'
    assert '-vf' not in cmd  # Original dimensions, no 720p downscale.
    assert has_alpha(item)
    assert stale_proxy({**item, 'proxy_path': 'old.mp4'})
    assert not stale_proxy({**item, 'proxy_path': 'new.webm'})


def test_opaque_proxy_does_not_gain_alpha_and_uses_better_quality(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('lumina.media.jobs._run', lambda cmd, **kw: calls.append(cmd))
    item = {'kind': 'video', 'path': 'video.mov', 'video': {'pix_fmt': 'yuv422p10le'}, 'audio': None}
    output = make_proxy(item, tmp_path)
    cmd = calls[0]
    assert output.suffix == '.mp4' and not has_alpha(item)
    assert '1080' in cmd[cmd.index('-vf') + 1]
    assert cmd[cmd.index('-crf') + 1] == '18'


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg integration')
def test_real_mov_alpha_survives_proxy_encoding(tmp_path):
    from PIL import Image
    from lumina.media.probe import probe, ffmpeg_bin

    rgba = Image.new('RGBA', (64, 32), (0, 0, 0, 0))
    rgba.paste((230, 35, 55, 255), (16, 8, 48, 24))
    png, mov = tmp_path / 'title.png', tmp_path / 'title.mov'
    rgba.save(png)
    subprocess.run([ffmpeg_bin(), '-v', 'error', '-loop', '1', '-i', str(png), '-t', '0.1',
                    '-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le', str(mov)],
                   check=True, capture_output=True, timeout=30)
    info = probe(str(mov))
    proxy = make_proxy({**info, 'path': str(mov)}, tmp_path)
    assert (probe(str(proxy))['video']['width'], probe(str(proxy))['video']['height']) == (64, 32)
    decoded = subprocess.run([ffmpeg_bin(), '-v', 'error', '-c:v', 'libvpx-vp9', '-i', str(proxy),
                              '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'],
                             capture_output=True, check=True, timeout=30).stdout
    assert decoded[3] == 0
    center = (16 * 64 + 32) * 4
    assert decoded[center + 3] == 255
    assert all(abs(decoded[center + i] - value) <= 8 for i, value in enumerate((230, 35, 55)))
