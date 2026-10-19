"""Coordinate-contract checks only; no reconstruction or accuracy claim."""
import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location('semantic_surface_support', Path(__file__).parents[2] / 'deploy/spatial/research/semantic_surface_support.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_colmap_corner_origin_becomes_native_center_coordinate():
    np.testing.assert_allclose(runner.native_pixel([.5, .5], [640, 480], [640, 480]), [0, 0])
    np.testing.assert_allclose(runner.native_pixel([639.5, 479.5], [640, 480], [640, 480]), [639, 479])


def test_resize_scales_corner_before_subtracting_half_pixel():
    # Nonuniform resize catches both wrong axis order and subtract-before-scale.
    np.testing.assert_allclose(runner.native_pixel([.5, 1.5], [640, 480], [1920, 960]), [1., 2.5])


def test_sampling_preserves_label_zero_and_rejects_outside_image():
    mask = np.array([[0, 7, 8], [9, 10, 11]], dtype=np.uint8)
    assert runner.sample_label(mask, [0, 0]) == 0
    assert runner.sample_label(mask, [2, 1]) == 11
    assert runner.sample_label(mask, [-.50001, 0]) is None
    assert runner.sample_label(mask, [2.5, 1]) is None
    assert runner.sample_label(mask, [0, 1.5]) is None


def test_nonfinite_feature_is_unknown_instead_of_label_zero():
    mask = np.zeros((2, 2), dtype=np.uint8)
    for pixel in ([float('nan'), 0], [0, float('inf')], [-float('inf'), 0]):
        assert runner.sample_label(mask, pixel) is None
