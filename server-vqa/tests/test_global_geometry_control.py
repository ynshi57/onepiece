import importlib.util
from pathlib import Path
import pytest
pytest.importorskip('scipy',reason='--with-spatial-geometry required')
pytest.importorskip('pycolmap',reason='--with-spatial-geometry required')
import numpy as np

spec=importlib.util.spec_from_file_location('global_geometry_control',Path(__file__).resolve().parents[2]/'deploy/spatial/research/global_geometry_control.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_spatial_patch_groups_cannot_split_close_landmarks():
    points=np.array([[0,0,0],[.01,0,0],[.02,0,0],[1,0,0]])
    ids=m.groups(points,.011)
    assert ids[0]==ids[1]==ids[2] and ids[3]!=ids[0]


def test_score_point_uses_disjoint_observations_and_keeps_failure():
    r=np.repeat(np.eye(3)[None],4,axis=0);t=np.array([[0,0,0],[-.2,0,0],[-.4,0,0],[-.6,0,0]])
    k=np.repeat(np.array([[500,500,320.5,240.5]]),4,axis=0);points=np.array([[.1,.2,3],[.3,.4,3]])
    ci=np.array([0,1,2,3,0]);pi=np.array([0,0,0,0,1]);xy=m.project(points[pi],ci,r,t,k)[0];xy[1,0]+=4
    records,per,over=m.score(points,ci,pi,xy,r,t,k,[0,1])
    assert records[0]['status']=='measured' and records[0]['scoringObservations']==2
    np.testing.assert_allclose(records[0]['errorsPixels'],[4,0],atol=1e-8)
    assert records[1]['status']=='failed' and records[1]['reason']=='insufficient_disjoint_observations'
    assert len(over)==2 and set(per)=={1,3}


def test_shared_intrinsics_are_one_session_vector_not_per_camera():
    k=np.array([[500,510,320,240],[520,530,330,250]],float)
    adjusted=m.shared_k(k,np.array([np.log(1.02),np.log(.98),3,-2]))
    np.testing.assert_allclose(adjusted[:,:2],k[:,:2]*[1.02,.98])
    np.testing.assert_allclose(adjusted[:,2:],k[:,2:]+[3,-2])


def test_fixed_camera_gauge_and_scoring_landmark_never_move():
    r=np.repeat(np.eye(3)[None],3,axis=0);t=np.array([[0,0,0],[-.3,0,0],[-.6,0,0]])
    k=np.repeat(np.array([[500,500,320,240]],float),3,axis=0)
    p=np.array([[.1,.2,3],[.3,.1,4],[.1,-.2,5],[.4,.3,4]],float)
    ci=np.tile(np.arange(3),4);pi=np.repeat(np.arange(4),3);xy=m.project(p[pi],ci,r,t,k)[0]
    wrong=p.copy();wrong[0,0]+=.01
    pp,rr,tt,kk,meta=m.fit_arm(wrong,ci,pi,xy,r,t,k,[0,1,2],[0,2],free_k=False,cpu_budget=10,max_evaluations=10)
    np.testing.assert_array_equal(pp[3],wrong[3]);np.testing.assert_array_equal(rr[[0,2]],r[[0,2]]);np.testing.assert_array_equal(tt[[0,2]],t[[0,2]])
    np.testing.assert_array_equal(kk,k)
    assert np.linalg.norm(pp[0]-p[0])<np.linalg.norm(wrong[0]-p[0])


def test_full_tracks_union_by_training_pixel_patch_not_world_distance():
    ci=np.array([0,0,1,1]);pi=np.array([0,1,1,2]);xy=np.array([[10,10],[17,10],[100,100],[107,100]],float)
    ids=m.pixel_groups(ci,pi,xy,4)
    assert ids[0]==ids[1]==ids[2] and ids[3]!=ids[0]


def test_partition_rejects_boolean_duplicate_range_and_overlap():
    import pytest
    for train,held in [([True],[2]),([1,1],[2]),([4],[2]),([1],[1])]:
        with pytest.raises(ValueError):m.validate_partition(train,held,4)


def test_timeout_never_returns_finite_difference_trial_as_accepted():
    r=np.repeat(np.eye(3)[None],3,axis=0);t=np.array([[0,0,0],[-.2,0,0],[-.4,0,0]])
    k=np.repeat(np.array([[500,500,320,240]],float),3,axis=0);p=np.array([[.1,.1,3]])
    ci=np.arange(3);pi=np.zeros(3,dtype=int);xy=m.project(p[pi],ci,r,t,k)[0]
    pp,rr,tt,kk,meta=m.fit_arm(p,ci,pi,xy,r,t,k,[0],[0,2],free_k=True,cpu_budget=-1)
    assert meta['accepted'] is False and meta['status']=='cpu_budget_exceeded_no_accepted_solution'
    np.testing.assert_array_equal(pp,p);np.testing.assert_array_equal(kk,k)
