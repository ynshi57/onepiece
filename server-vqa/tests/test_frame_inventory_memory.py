"""Protocol fixtures model interrupted and complete local VLM inventories; no inference."""
import importlib.util
from pathlib import Path

import pytest
from app.spatial_scene_memory import load, query, save

spec = importlib.util.spec_from_file_location('frame_inventory_memory', Path(__file__).parents[2] / 'deploy/spatial/research/frame_inventory_memory.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def source():
    document = dict(id='room-1', keyframes=[dict(id='photo-45', pose=dict(epoch=2), time=23.5, width=1920, height=1440),
                                          dict(id='photo-46', pose=dict(epoch=2), time=24., width=1920, height=1440)])
    result = dict(scanID='room-1', totalFrameCount=2, requestedFrameCount=1, model='local-qwen3b', rows=[
        dict(frameIndex=0, frameID='photo-45', epoch=2, frameTime=23.5, status='processed', finishReason='stop',
             objects=[dict(name='钢琴', evidence='照片左边有琴键和琴身', uncertain=False)])])
    return document, result


def test_inventory_round_trip_retains_provenance_without_invented_geometry(tmp_path):
    data = runner.convert(*source())
    path = tmp_path / 'inventory.json'
    save(path, data)
    answer = query(load(path), '钢琴')
    item = answer['candidates'][0]
    assert (item['scanID'], item['frameID'], item['epoch'], item['frameTime']) == ('room-1', 'photo-45', 2, 23.5)
    assert item['capturedAt'] is None
    assert all(item[key] is None for key in ('labelID', 'pixelBBox', 'pixelCount', 'modelScore', 'worldPosition', 'entityID'))
    assert item['evidence'] == '照片左边有琴键和琴身'
    assert answer['validationStatus'] == 'unverified_model_observations'
    assert answer['unprocessedFrameCount'] == 1


def test_truncated_processed_response_cannot_enter_memory():
    document, result = source()
    result['rows'][0]['finishReason'] = 'length'
    with pytest.raises(ValueError, match='incomplete model response'):
        runner.convert(document, result)


def test_failure_preserved_and_cannot_carry_candidates():
    document, result = source()
    row = result['rows'][0]
    row.update(status='failed', failure='runtime unavailable')
    with pytest.raises(ValueError, match='failed model response'):
        runner.convert(document, result)
    row['objects'] = []
    answer = query(runner.convert(document, result), '钢琴')
    assert answer['observationStatus'] == 'no_usable_observations'
    assert answer['absenceEstablished'] is False
    assert answer['failedFrames'][0]['failure'] == 'runtime unavailable'


def test_cross_scan_or_wrong_epoch_cannot_rebind_inventory():
    document, result = source()
    result['scanID'] = 'other-room'
    with pytest.raises(ValueError, match='source scan mismatch'):
        runner.convert(document, result)
    result['scanID'] = document['id']
    result['rows'][0]['epoch'] = 3
    with pytest.raises(ValueError, match='provenance mismatch'):
        runner.convert(document, result)


def test_inventory_cannot_smuggle_3d_or_object_geometry():
    document, result = source()
    result['rows'][0]['worldPosition'] = [1, 2, 3]
    with pytest.raises(ValueError, match='identity or 3D'):
        runner.convert(document, result)
    del result['rows'][0]['worldPosition']
    result['rows'][0]['objects'][0]['pixelBBox'] = [0, 0, 1, 1]
    with pytest.raises(ValueError, match='invalid inventory item'):
        runner.convert(document, result)


def test_cropped_inventory_cannot_silently_lose_its_source_region():
    document, result = source()
    result['rows'][0]['cropBBoxNative'] = [100, 200, 900, 1100]
    with pytest.raises(ValueError, match='crop-aware provenance'):
        runner.convert(document, result)
