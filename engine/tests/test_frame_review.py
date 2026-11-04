import json
import subprocess

import pytest
from PIL import Image

from lumina.frame_review import bounds, inspect
from lumina.commands.registry import CommandError
from lumina.media.probe import ffmpeg_bin
from lumina.adb.quiet_process import NO_WINDOW


@pytest.fixture
def source(tmp_path):
    # One unwanted blue frame surrounded by red; a keyframe sheet can miss it.
    for n in range(10):
        Image.new('RGB', (64, 36), 'blue' if n == 6 else 'red').save(tmp_path / f'{n:03d}.png')
    path = tmp_path / 'single-frame.mkv'
    subprocess.run([ffmpeg_bin(), '-v', 'error', '-framerate', '30000/1001', '-i',
                    str(tmp_path / '%03d.png'), '-c:v', 'ffv1', str(path)], check=True, **NO_WINDOW)
    return path


def test_consecutive_frame_and_pts_evidence(source):
    result = inspect('inspect_source_frames', {'path': str(source), 'start_frame': 4, 'count': 5}, None)
    meta = json.loads(result.content[-1].text)
    assert result.content[0].type == 'image'
    assert [f['frame'] for f in meta['frames']] == [4, 5, 6, 7, 8]
    assert meta['frames'][2]['mean_rgb_change'] > .5
    assert meta['frames'][3]['mean_rgb_change'] > .5
    assert meta['frames'][1]['mean_rgb_change'] < .01
    assert meta['frames'][2]['pts_seconds'] == pytest.approx(6 * 1001 / 30000, abs=.001)
    assert meta['complete'] and meta['verdict'] == 'not_assessed'


def test_sampling_and_eof_are_not_clean_verdicts(source):
    result = inspect('inspect_source_frames', {'path': str(source), 'start_frame': 7, 'count': 4, 'step': 2}, None)
    meta = json.loads(result.content[-1].text)
    assert [f['frame'] for f in meta['frames']] == [7, 9]
    assert not meta['complete']
    assert meta['coverage'] == 'sampled_not_boundary_proof'
    with pytest.raises(CommandError):
        inspect('inspect_source_frames', {'path': str(source), 'start_frame': 100}, None)


@pytest.mark.parametrize('args', [{'start_frame': -1}, {'start_frame': 0, 'count': 25}, {'start_frame': True}, {'start_frame': 0, 'step': 0}])
def test_invalid_ranges(args):
    with pytest.raises(CommandError):
        bounds(args)


def test_sequence_frames_are_explicit_and_temporary_images_removed(tmp_path):
    class Engine:
        def dispatch(self, method, args):
            if method == 'project.list':
                return {'active': 'test'}
            if method == 'timeline.state':
                return {'clips': []}
            assert args['sequence_id'] == 'seq'
            n = int(args['time'][:-1])
            path = tmp_path / f'{n}.png'
            Image.new('RGB', (64, 36), 'red').save(path)
            return {'path': str(path)}
    result = inspect('inspect_timeline_frames', {'sequence_id': 'seq', 'start_frame': 9, 'count': 2}, Engine())
    assert [r['frame'] for r in json.loads(result.content[-1].text)['frames']] == [9, 10]
    assert not list(tmp_path.glob('*.png'))
