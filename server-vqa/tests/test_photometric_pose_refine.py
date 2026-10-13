"""Renderer and se(3) recovery on a synthetic Gaussian; not scene quality."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    'photometric_pose_refine',
    Path(__file__).resolve().parents[2] / 'deploy/spatial/research/photometric_pose_refine.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def blob():
    means = np.array([[0.0, 0.0, 1.0]])
    return dict(means=means, log_scales=np.full((1, 3), -3.0),
                quat=np.array([[1.0, 0.0, 0.0, 0.0]]),
                colors=np.array([[1.0, 0.0, 0.0]]), opacity=np.array([0.95]))


def test_single_gaussian_projects_to_principal_point():
    w2c = np.eye(4)
    image = m.render(blob(), w2c, 50, 50, 16, 12, 32, 24, limit=1, background=0.0)
    peak = np.unravel_index(np.argmax(image[..., 0]), image.shape[:2])
    assert peak == (12, 16)
    assert image[12, 16, 0] > image[12, 16, 1]


def test_behind_camera_stays_background():
    model = blob()
    model['means'] = np.array([[0.0, 0.0, -1.0]])
    image = m.render(model, np.eye(4), 50, 50, 16, 12, 32, 24, limit=1, background=1.0)
    assert np.allclose(image, 1.0)


def test_opengl_c2w_looking_along_minus_z_matches_opencv_identity():
    c2w_gl = np.eye(4)
    w2c = m.world_to_cv_camera(c2w_gl)
    # OpenGL identity looks along -Z; OpenCV +Z-forward w2c should flip Y and Z.
    np.testing.assert_allclose(w2c, np.diag([1.0, -1.0, -1.0, 1.0]))


def test_se3_recovery_moves_toward_perturbed_camera():
    model = blob()
    truth_w2c = np.eye(4)
    frame = dict(fl_x=80.0, fl_y=80.0, cx=16.0, cy=12.0, w=32, h=24)
    truth = m.render(model, truth_w2c, frame['fl_x'], frame['fl_y'], frame['cx'], frame['cy'],
                     32, 24, limit=1, background=0.0)
    wrong = m.se3(np.array([0.0, 0.0, 0.0, 0.03, 0.0, 0.0])) @ truth_w2c
    before = m.psnr(truth, m.render(model, wrong, frame['fl_x'], frame['fl_y'],
                                    frame['cx'], frame['cy'], 32, 24, limit=1, background=0.0))
    result = m.refine_camera(model, wrong, frame, truth, translation=0.02, rotation=0.01,
                             steps=6, background=0.0)
    assert result['psnr'] > before
    assert abs(result['xi'][3]) > abs(result['xi'][0])


def test_rotated_anisotropic_visible_gaussian_is_not_discarded():
    model = blob()
    model['log_scales'] = np.log(np.array([[0.05, 0.10, 0.15]]))
    model['quat'] = np.array([[np.cos(np.pi / 8), 0, 0, np.sin(np.pi / 8)]])
    image = m.render(model, np.eye(4), 100, 100, 32, 32, 64, 64, limit=1, background=0.0)
    red = image[..., 0]
    assert red[32, 32] > 0.9
    assert np.count_nonzero(red > 0.01) > 100
    # For a 45-degree rotation of unequal axes, the projected ellipse has
    # negative xy covariance; check the actual rendered pixels, not a call.
    y, x = np.indices(red.shape)
    xy = np.sum(red * (x - 32) * (y - 32)) / np.sum(red)
    assert xy < -1


def test_fit_views_in_validation_export_are_never_labeled_holdout():
    frame = lambda i: dict(file_path=f'images/{i:05d}.jpg')
    train = [frame(155), frame(162)]
    mixed = [frame(154), frame(155)]
    held = m.diagnostic_frames(train, mixed)
    assert held == [(frame(154), 'heldout')]
    both = m.diagnostic_frames(train, mixed, include_fit=True, wanted={155, 162})
    assert both == [(frame(154), 'heldout'), (frame(155), 'training_fit'), (frame(162), 'training_fit')]


def test_duplicate_validation_identity_is_explicit_failure():
    with pytest.raises(ValueError, match='duplicate_validation_frame_identity'):
        m.diagnostic_frames([], [dict(file_path='images/00154.jpg')] * 2)


def test_load_frame_noninteger_resize_preserves_pixel_center_projection(tmp_path):
    from PIL import Image
    # An actual image undergoes PIL resize, with independently rounded axes.
    Image.new('RGB', (101, 77), (120, 60, 30)).save(tmp_path / 'native.png')
    source = dict(file_path='native.png', fl_x=83.7, fl_y=80.2, cx=48.3, cy=39.1,
                  w=101, h=77, transform_matrix=np.eye(4).tolist())
    resized, rgb, w2c = m.load_frame(tmp_path, source, resolution=61)
    assert rgb.shape == (47, 61, 3)
    assert (resized['w'], resized['h']) == (61, 47)
    # Projection of real 3D rays must land at PIL's pixel-center locations,
    # including the half-pixel translation, not merely scaled principal points.
    rays = np.array([[0., 0., 1.], [.21, -.17, 1.2], [-.33, .27, 2.]])
    native = np.column_stack((83.7 * rays[:, 0] / rays[:, 2] + 48.3,
                              80.2 * rays[:, 1] / rays[:, 2] + 39.1))
    expected = (native + .5) * np.array([61 / 101, 47 / 77]) - .5
    projected = np.column_stack((resized['fl_x'] * rays[:, 0] / rays[:, 2] + resized['cx'],
                                 resized['fl_y'] * rays[:, 1] / rays[:, 2] + resized['cy']))
    np.testing.assert_allclose(projected, expected, atol=1e-12)
    assert np.max(np.abs(projected - native * [61 / 101, 47 / 77])) > .19
    assert source['cx'] == 48.3 and source['cy'] == 39.1
    np.testing.assert_allclose(w2c, np.diag([1., -1., -1., 1.]))
