"""Near-real multi-frame semantic regions; synthetic geometry, no 3D truth claim."""
from copy import deepcopy

import pytest

from app.spatial_scene_memory import build_observation_set, load, query, save, validate


def sample():
    frames = [dict(scanID='scan-room', frameID=f'f{i}', epoch=0, frameTime=i * .5,
                   capturedAt=None, status='processed' if i < 2 else 'failed',
                   failure=None if i < 2 else 'inference failed', imageWidth=640,
                   imageHeight=480) for i in range(3)]
    observations = []
    for i in range(2):
        observations.append(dict(scanID='scan-room', observationID=f'o{i}', frameID=f'f{i}',
                                 epoch=0, frameTime=i * .5, capturedAt=None,
                                 modelID='segformer-ade20k', labelID=19, label='chair',
                                 pixelBBox=[.1 + i * .05, .2, .6, .8], modelScore=.81,
                                 pixelCount=22010, imageWidth=640, imageHeight=480,
                                 regionKind='semanticRegionCandidate', entityID=None,
                                 worldPosition=None))
    return build_observation_set(scan_id='scan-room', total_frame_count=20,
                                 requested_frame_ids=['f0', 'f1', 'f2'], frames=frames,
                                 observations=observations)


def test_candidates_are_not_merged_or_promoted_to_3d(tmp_path):
    data = sample()
    path = tmp_path / 'observations.json'
    save(path, data)
    restored = load(path)
    assert restored == data
    answer = query(restored, 'CHAIR')
    assert len(answer['candidates']) == 2
    assert [x['frameID'] for x in answer['candidates']] == ['f0', 'f1']
    assert [x['frameTime'] for x in answer['candidates']] == [0, .5]
    assert all(x['capturedAt'] is None for x in answer['candidates'])
    assert answer['spatialStatus'] == 'unknown3D'
    assert answer['instanceIdentity'] == 'unknown'
    assert answer['totalFrameCount'] == 20
    assert answer['processedFrameCount'] == 2
    assert restored['frames'][2]['failure'] == 'inference failed'
    answer['candidates'][0]['label'] = 'changed'
    assert data['observations'][0]['label'] == 'chair'


@pytest.mark.parametrize('field,value', [
    ('pixelBBox', [0, 0, 1.1, 1]), ('pixelBBox', [.5, 0, .1, 1]),
    ('pixelBBox', [0, 0, float('nan'), 1]), ('modelScore', float('inf')),
    ('scanID', 'other-scan'), ('frameID', 'f2'), ('epoch', 1),
    ('worldPosition', [1, 2, 3]), ('entityID', 'chair-1'),
    ('pixelCount', 640 * 480 + 1),
])
def test_rejects_invalid_or_invented_observation(field, value):
    data = sample()
    data['observations'][0][field] = value
    with pytest.raises(ValueError):
        validate(data)


@pytest.mark.parametrize('mutate', [
    lambda d: d['observations'].append(deepcopy(d['observations'][0])),
    lambda d: d['frames'].append(deepcopy(d['frames'][0])),
    lambda d: d['requestedFrameIDs'].append('f0'),
    lambda d: d.update(schemaVersion=3),
    lambda d: d['frames'][0].update(scanID='other-scan'),
    lambda d: d['frames'][0].update(frameTime=float('nan')),
    lambda d: d['frames'][0].update(capturedAt='2026-10-16T10:00:00'),
    lambda d: d['frames'].pop(),
])
def test_rejects_inconsistent_accounting(mutate):
    data = sample()
    mutate(data)
    with pytest.raises(ValueError):
        validate(data)


def test_invalid_save_preserves_existing_sidecar(tmp_path):
    path = tmp_path / 'memory.json'
    data = sample()
    save(path, data)
    broken = deepcopy(data)
    broken['observations'][0]['modelScore'] = float('nan')
    with pytest.raises(ValueError):
        save(path, broken)
    assert load(path) == data


def test_query_empty_is_not_absence_and_preserves_failed_receipts():
    data = sample()
    answer = query(data, 'table')
    assert answer['observationStatus'] == 'label_not_observed'
    assert answer['absenceEstablished'] is False
    assert answer['unprocessedFrameCount'] == 17
    assert answer['failedFrames'] == [data['frames'][2]]
    assert query(data, 'chair')['observationStatus'] == 'candidate_observations'
    data['observations'] = []
    for frame in data['frames']:
        frame.update(status='failed', failure='model runtime unavailable')
    answer = query(data, 'chair')
    assert answer['observationStatus'] == 'no_usable_observations'
    assert answer['absenceEstablished'] is False
    assert answer['processedFrameCount'] == 0
    assert len(answer['failedFrames']) == 3
    assert answer['spatialStatus'] == 'unknown3D'
    assert answer['instanceIdentity'] == 'unknown'
    answer['failedFrames'][0]['failure'] = 'changed'
    assert data['frames'][0]['failure'] == 'model runtime unavailable'


def test_processed_without_regions_also_has_no_usable_observations():
    data = sample()
    data['observations'] = []
    assert query(data, 'chair')['observationStatus'] == 'no_usable_observations'


def frame_sample():
    data = sample()
    item = data['observations'][0]
    item.update(regionKind='semanticFrameCandidate', label='电脑', modelID='qwen3b',
                labelID=None, pixelBBox=None, modelScore=None, pixelCount=None,
                evidence='桌上看到电脑和显示器', uncertain=False)
    data['observations'] = [item]
    return validate(data)


def test_v1_region_sidecar_remains_readable(tmp_path):
    data = sample()
    data['schemaVersion'] = 1
    path = tmp_path / 'v1.json'
    save(path, data)
    assert load(path) == data
    assert len(query(load(path), 'chair')['candidates']) == 2


def test_v2_frame_candidate_round_trip_is_unverified(tmp_path):
    data = frame_sample()
    assert data['schemaVersion'] == 2
    path = tmp_path / 'vlm.json'
    save(path, data)
    answer = query(load(path), '电脑')
    assert answer['validationStatus'] == 'unverified_model_observations'
    assert answer['spatialStatus'] == 'unknown3D'
    assert answer['instanceIdentity'] == 'unknown'
    assert answer['candidates'][0]['uncertain'] is False
    assert answer['candidates'][0]['pixelBBox'] is None
    assert answer['candidates'][0]['frameID'] == 'f0'
    assert answer['candidates'][0]['evidence'] == '桌上看到电脑和显示器'


@pytest.mark.parametrize('field,value', [
    ('pixelBBox', [0, 0, 1, 1]), ('modelScore', .99), ('pixelCount', 100),
    ('labelID', 19), ('uncertain', 0), ('uncertain', 'false'), ('evidence', ''),
])
def test_frame_candidate_rejects_invented_measurements(field, value):
    data = frame_sample()
    data['observations'][0][field] = value
    with pytest.raises(ValueError):
        validate(data)


def test_frame_variant_not_permitted_in_v1_or_region_fields():
    data = frame_sample()
    data['schemaVersion'] = 1
    with pytest.raises(ValueError):
        validate(data)
    data = sample()
    data['observations'][0]['uncertain'] = False
    with pytest.raises(ValueError):
        validate(data)
