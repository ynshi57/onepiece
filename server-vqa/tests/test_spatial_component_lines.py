import numpy as np
from app.spatial_component_lines import _fit
from app.spatial_line_structure import LineStructureConfig, _project
from app.spatial_reconstruction import Camera


def scene():
    k = np.array([[500., 0., 320.], [0., 500., 240.], [0., 0., 1.]])
    prepared = {}
    observations = []
    endpoints = np.array([[-.5, 0., 2.], [.5, 0., 2.]])
    for index, center in enumerate(([0., -.2, 0.], [.2, .2, 0.], [-.2, .4, 0.])):
        t = np.eye(4); t[:3, 3] = center
        cam = Camera(k, t, np.linalg.inv(t), 0, float(index), 640, 480)
        prepared[index] = (cam, k)
        uv, _ = _project(cam, k, endpoints)
        observations.append((index, 0, uv))
    return prepared, observations


def test_recovers_finite_segment_from_actual_observed_intervals():
    prepared, observations = scene()
    result = _fit(observations, prepared, LineStructureConfig())
    assert result['reason'] == 'component_finite_line_supported'
    assert np.allclose(sorted(result['endpointsWorld']), [[-.5, 0., 2.], [.5, 0., 2.]])
    assert len(result['finiteTrainingIntervals']) == 3
    assert result['p90ResidualPixels'] < 1e-8


def test_disjoint_finite_segments_do_not_make_infinite_line_supported():
    prepared, observations = scene()
    endpoints = np.array([[1., 0., 2.], [1.5, 0., 2.]])
    uv, _ = _project(prepared[2][0], prepared[2][1], endpoints)
    observations[2] = (2, 0, uv)
    result = _fit(observations, prepared, LineStructureConfig())
    assert result['reason'] == 'insufficient_finite_training_overlap'
    assert result['finiteOverlapMeters'] < 0


def test_same_camera_planes_are_explicitly_degenerate():
    prepared, observations = scene()
    repeated = [observations[0]] * 3
    assert _fit(repeated, prepared, LineStructureConfig())['reason'] == 'degenerate_backprojection_planes'


def test_refinement_retains_unavailable_candidate_and_rejects_eval_camera():
    from dataclasses import asdict
    import pytest
    from app.spatial_component_lines import refine_component_lines
    prepared, observations = scene()
    cams = {i: x[0] for i, x in prepared.items()}
    candidate = {'id': 'frozen', 'sourceFrames': [0, 1], 'jointFit': {
        'observations': [{'frameIndex': i, 'lineId': 0} for i in range(3)]}}
    training = {'status': 'training_only_local_line_candidates', 'evaluationRGBUsed': False,
                'trainingFrameIndices': [0, 1, 2], 'config': asdict(LineStructureConfig()),
                'frames': [{'frameIndex': i, 'longLines': [s.tolist()]} for i, _, s in observations],
                'candidates': [candidate], 'localLines': [{'candidateId': 'frozen'}]}
    document = {'keyframes': [{'width': 640, 'height': 480} for _ in range(4)]}
    result = refine_component_lines(document, training, {0: cams[0]}, 0)
    assert result['candidateCount'] == 1
    assert result['lines'][0]['reason'] == 'source_observation_not_in_component'
    assert result['lines'][0]['missingComponentObservationFrames'] == [1, 2]
    with pytest.raises(ValueError, match='outside_frozen_training_partition'):
        refine_component_lines(document, training, {3: cams[0]}, 0)
