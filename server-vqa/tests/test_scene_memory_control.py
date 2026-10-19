"""Near-real mask cases; not a claim about model accuracy or spatial identity."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest

path = Path(__file__).parents[2] / 'deploy/spatial/research/scene_memory_control.py'
spec = importlib.util.spec_from_file_location('scene_memory_control', path)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def frame():
    return dict(scanID='room-scan', frameID='photo-a', epoch=2, frameTime=12.5,
                capturedAt=None, imageWidth=4, imageHeight=3)


def test_disconnected_same_class_keeps_separate_unknown_identity_regions():
    # Two separated chair silhouettes with floor between, plus a 1-pixel fragment.
    labels = np.array([[1,0,1,0],[1,0,1,0],[0,0,0,1]], dtype=np.uint8)
    rows = control.regions(labels, np.full(labels.shape,.6), frame(), {0:'floor',1:'chair'}, 'local-model')
    chairs = [r for r in rows if r['label']=='chair']
    assert len(chairs)==3
    assert sorted(r['pixelCount'] for r in chairs)==[1,2,2]
    assert sum(r['pixelCount'] for r in rows)==labels.size
    assert all(r['entityID'] is None and r['worldPosition'] is None for r in rows)
    assert chairs[0]['pixelBBox']==[0,0,.25,2/3]


def test_region_bbox_uses_top_left_axes_and_scores_only_its_pixels():
    labels = np.array([[0,0,0,0],[0,1,1,0],[0,0,0,0]], dtype=np.uint8)
    scores = np.array([[.1]*4,[.1,.4,.8,.1],[.1]*4])
    target = next(r for r in control.regions(labels,scores,frame(),{0:'floor',1:'table'},'local-model') if r['label']=='table')
    assert target['pixelBBox']==[.25,1/3,.75,2/3]
    assert target['modelScore']==pytest.approx(.6)
    assert target['frameTime']==12.5 and target['capturedAt'] is None


def test_invalid_model_map_is_not_saved_as_success():
    with pytest.raises(ValueError):
        control.regions(np.ones((2,2)),np.full((2,2),float('nan')),frame(),{1:'chair'},'local-model')
    with pytest.raises(ValueError):
        control.regions(np.ones((2,2)),np.ones((3,2)),frame(),{1:'chair'},'local-model')
