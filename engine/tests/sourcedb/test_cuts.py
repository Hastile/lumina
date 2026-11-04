from fractions import Fraction

from lumina.sourcedb import ingest


def _scores(spec, fps=60, seconds=3):
    """Scene scores at `fps`; spec maps a frame number to its score, the rest are still frames."""
    return [(Fraction(i, fps), spec.get(i, 0.0002)) for i in range(fps * seconds)]


def test_a_dark_cut_among_still_frames_is_a_cut():
    assert ingest.spike_times(_scores({90: 0.18, 120: 0.01})) == [Fraction(90, 60)]


def test_animation_steps_of_the_same_size_are_not_cuts():
    # drawings held for 3 frames, each change about as big as the last
    spec = {i: 0.12 + 0.02 * (i % 2) for i in range(0, 180, 3)}
    assert ingest.spike_times(_scores(spec)) == []


def test_a_cut_already_above_the_scene_threshold_is_left_to_it():
    assert ingest.spike_times(_scores({90: 0.6})) == []
