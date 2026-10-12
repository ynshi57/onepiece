"""Analytic projection/rejection mechanism tests, never product acceptance evidence."""
import importlib.util
from pathlib import Path
import sys
import numpy as np
import pytest

path=Path(__file__).resolve().parents[2]/'deploy/spatial/research/restore_photo_check.py'
spec=importlib.util.spec_from_file_location('restore_photo_check',path)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

def scene():
    image=np.random.default_rng(11).uniform(0,1,(96,128))
    k=np.array([[100.,0,63.5],[0,100.,47.5],[0,0,1.]])
    return image,k,np.eye(4)

def check(image,k,pose,query_pose=None):
    return m.check_patch(image,image,k,pose,k,pose if query_pose is None else query_pose,[0,0,1],-3,[40,30,31,31])

def test_actual_pinhole_plane_projection():
    _,k,pose=scene();query=pose.copy();query[0,3]=-.3
    pixels=np.array([[63.5,47.5],[83.5,57.5]])
    uv,valid=m.plane_warp(pixels,k,pose,k,query,[0,0,1],-3)
    assert valid.all();np.testing.assert_allclose(uv,pixels+[-10,0],atol=1e-10)

def test_identity_unique_texture_supported_only_experimentally():
    image,k,pose=scene();r=check(image,k,pose)
    assert r['verdict']=='supported';assert r['predictedCorrelation']==pytest.approx(1)
    assert r['best']['offset']==[0,0];assert len(r['search'])==625
    assert not r['productAccepted'] and not r['thresholdsCalibrated'] and not r['planeHitsAreGroundTruth']

def test_periodic_texture_cannot_verify_location():
    image,k,pose=scene();image=np.tile(np.arange(128)%2,(96,1)).astype(float)
    r=check(image,k,pose)
    assert r['verdict']=='insufficientEvidence' and r['reason']=='ambiguous_match'
    assert r['peakRadiusPixels']>3

def test_blank_wall_is_insufficient_not_restored():
    image,k,pose=scene();r=check(np.ones_like(image)*.5,k,pose)
    assert r['verdict']=='insufficientEvidence' and r['reason']=='low_reference_texture'

def test_out_of_frame_keeps_full_evidence():
    image,k,pose=scene();query=pose.copy();query[0,3]=30
    r=check(image,k,pose,query)
    assert r['verdict']=='insufficientEvidence';assert r['validFraction']==0
    assert len(r['search'])==625 and r['best'] is None

def test_wrong_pose_rejection_mechanism_not_real_room_negative():
    image,k,pose=scene();query=pose.copy();query[0,3]=-.18
    r=check(image,k,pose,query)
    assert r['best']['offset']==[6,0]
    assert r['verdict']=='inconsistent' and r['reason']=='match_disagrees_with_ar_prediction'

def test_resize_half_pixel_intrinsics():
    image,k,_=scene();_,scaled,ratio=m.resize_image(image,k,64)
    assert ratio==(.5,.5);assert scaled[0,2]==pytest.approx((k[0,2]+.5)*.5-.5)

def test_frozen_reference_leakage_guard(tmp_path):
    with pytest.raises(ValueError,match='training_evaluation_overlap'):
        m.run_manifest({'trainingFrameIndices':[1],'evaluationFrameIndices':[1]},tmp_path)
    with pytest.raises(ValueError,match='training_frozen_reference_required'):
        m.run_manifest({'trainingFrameIndices':[1],'evaluationFrameIndices':[2],'patchesFrozenFromTraining':True,'reference':{'frameIndex':2}},tmp_path)
