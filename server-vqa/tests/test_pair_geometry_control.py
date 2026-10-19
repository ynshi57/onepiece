"""Diagnostic mathematics on known cameras; not real-world accuracy acceptance."""
import copy
import importlib.util
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip('pycolmap', reason='optional --with-spatial-geometry research profile required')

spec=importlib.util.spec_from_file_location('pair_geometry_control',Path(__file__).resolve().parents[2]/'deploy/spatial/research/pair_geometry_control.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def frames_and_points(baseline=.25):
    k=np.array([[500.,0,320],[0,500,240],[0,0,1.]])
    frames=[]
    for index,x in enumerate((0.,baseline)):
        t=np.diag([1.,-1.,-1.,1.]);t[0,3]=x
        frames.append(dict(intrinsics=k.flatten(order='F').tolist(),cameraToWorld=t.flatten(order='F').tolist(),
            width=640,height=480,time=float(index),pose=dict(time=float(index),epoch=1,
            position=dict(x=x,y=0.,z=0.),forward=dict(x=0.,y=0.,z=1.))))
    rng=np.random.default_rng(17)
    world=np.column_stack((rng.uniform(-1,1,200),rng.uniform(-.8,.8,200),rng.uniform(2,6,200)))
    pixels=[]
    for x in (0.,baseline):
        local=world-[x,0,0];q=local@k.T;pixels.append(q[:,:2]/q[:,2:])
    matches=np.column_stack((np.arange(len(world)),np.arange(len(world))))
    return frames,pixels,matches


def test_correct_cameras_score_and_translation_direction_error_is_visible():
    f,p,matches=frames_and_points()
    result=m.diagnose_pair(*f,*p,matches)
    assert result['recordedF']['medianPixels'] < 1e-9
    assert result['imageF']['medianPixels'] < 1e-6
    bad=copy.deepcopy(f[1]);t=np.array(bad['cameraToWorld']).reshape(4,4,order='F')
    t[1,3]=.15;bad['cameraToWorld']=t.flatten(order='F').tolist();bad['pose']['position']['y']=.15
    changed=m.diagnose_pair(f[0],bad,*p,matches)
    assert changed['recordedF']['medianPixels'] > 5
    assert changed['imageF']['medianPixels'] < 1e-6
    assert changed['matchCount']==len(matches)
    assert changed['fittingCount']+changed['scoringCount']==len(matches)


def test_zero_baseline_does_not_falsely_score_perfect_epipolar_geometry():
    f,p,matches=frames_and_points(0.)
    result=m.diagnose_pair(*f,*p,matches)
    assert result['recordedFReason']=='zero_baseline_undefined'
    assert result['recordedF']['measuredCount']==0
    assert result['recordedF']['undefinedCount']==result['scoringCount']
    assert result['recordedRotationH']['medianPixels'] < 1e-9


def test_reused_target_feature_cannot_leak_across_fit_score():
    f,p,matches=frames_and_points();matches[1,1]=matches[0,1]
    with pytest.raises(ValueError,match='non_unique_feature_identity'):
        m.diagnose_pair(*f,*p,matches)


def test_no_matches_is_retained_and_solver_not_claimed():
    f,p,_=frames_and_points()
    r=m.diagnose_pair(*f,*p,np.empty((0,2),int))
    assert r['status']=='no_matches'
    assert r['imageF']['status']=='insufficient_fit_or_scoring_matches'
    assert r['productAccepted'] is False


def test_structured_source_identity_does_not_accept_embedded_wrong_hash():
    import json
    good='a'*64
    assert m.cache_matches_source(good+'legacy-provenance',good)
    assert m.cache_matches_source(json.dumps({'imageSHA256':good,'maxSide':640}),good)
    assert not m.cache_matches_source(json.dumps({'imageSHA256':'b'*64,'note':good}),good)
    assert not m.cache_matches_source('{broken '+good,good)
