"""Temporal control mathematics; no claim of real SLAM accuracy."""
import importlib.util
from pathlib import Path
import copy
import numpy as np
import pytest

spec=importlib.util.spec_from_file_location('temporal_pose_correction',Path(__file__).resolve().parents[2]/'deploy/spatial/research/temporal_pose_correction.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def documents():
    raw={'keyframes':[]}
    for time in (0.,1.,2.):
        p=np.eye(4);p[0,3]=time
        raw['keyframes'].append(dict(cameraToWorld=p.flatten(order='F').tolist(),intrinsics=np.array([[100.,0,50],[0,100,50],[0,0,1]]).flatten(order='F').tolist(),width=100,height=100,time=time,pose=dict(epoch=1,time=time,position=dict(x=time,y=0,z=0),forward=dict(x=0,y=0,z=-1))))
    refined=copy.deepcopy(raw)
    for i,shift in ((0,0.),(2,.2)):
        p=np.array(refined['keyframes'][i]['cameraToWorld']).reshape(4,4,order='F');p[1,3]=shift;refined['keyframes'][i]['cameraToWorld']=p.flatten(order='F').tolist();refined['keyframes'][i]['pose']['position']['y']=shift
    return raw,refined

def test_frozen_training_correction_updates_eval_pose_without_eval_anchor():
    raw,refined=documents();before=copy.deepcopy(raw)
    out=m.interpolate_corrections(raw,refined,[dict(frameIndex=0,component=0),dict(frameIndex=2,component=0)],[0,2],[1])
    assert out['keyframes'][1]['pose']['position']['y']==pytest.approx(.1)
    assert raw==before
    assert out['temporalPoseCorrection']['evaluationRGBUsed'] is False


def test_validation_anchor_rejected():
    raw,refined=documents()
    with pytest.raises(ValueError,match='anchor_outside_training'):m.interpolate_corrections(raw,refined,[dict(frameIndex=0,component=0),dict(frameIndex=1,component=0)],[0,2],[1])


def test_disconnected_component_bridge_is_explicit_and_opt_in():
    raw,refined=documents();anchors=[dict(frameIndex=0,component=0),dict(frameIndex=2,component=1)]
    safe=m.interpolate_corrections(raw,refined,anchors,[0,2],[1]);control=m.interpolate_corrections(raw,refined,anchors,[0,2],[1],allow_component_bridge=True)
    assert safe['temporalPoseCorrection']['records'][1]['reason']=='nearest_anchor_component_bridge_not_interpolated'
    assert control['temporalPoseCorrection']['records'][1]['reason']=='experimental_component_bridge'
    assert control['temporalPoseCorrection']['componentsVisuallyConnected'] is False
