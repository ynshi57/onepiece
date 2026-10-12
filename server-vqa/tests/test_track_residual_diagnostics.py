"""Analytic native-camera observations; diagnostic association is not causal truth."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
spec=importlib.util.spec_from_file_location('track_residual_diagnostics',Path(__file__).resolve().parents[2]/'deploy/spatial/research/track_residual_diagnostics.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def scene():
    k=np.array([[700.,0,480.],[0,700.,360.],[0,0,1.]])
    camera={'frameIndex':0,'K':k.tolist(),'worldToCamera':np.eye(4).tolist(),'width':960,'height':720}
    tracks=[]
    # Distributed 2D covariates and varying positive depths, not identical ideal rays.
    pixels=[[180,160],[330,170],[490,160],[700,180],[200,310],[380,350],
            [520,360],[730,300],[180,540],[340,560],[540,530],[710,560]]
    for i,p in enumerate(pixels):
        row=(p[1]-360)/720.;radius2=((p[0]-480)**2+(p[1]-360)**2)/960**2
        residual=np.array([.3+2*row+3*radius2,-.2-1.5*row+2*radius2])
        point=np.linalg.solve(k,np.r_[p,1.])*(2.+i*.1)
        tracks.append({'id':str(i),'worldPoint':point.tolist(),
                       'observations':[{'frameIndex':0,'pixel':(p+residual).tolist()}]})
    return {'training_indices':[0],'evaluation_indices':[1],'cameras':[camera], 'tracks':tracks}


def test_group_validation_detects_known_trend_without_modifying_geometry():
    payload=scene();r=module.diagnose(payload);c=r['cameraReports'][0]
    assert c['reason']=='training_covariate_trend_only'
    assert np.allclose(c['coefficientsXY'],[[.3,-.2],[2,-1.5],[3,2]],atol=1e-10)
    assert c['groupLeaveOneOut']['fullyMeasured']
    assert c['groupLeaveOneOut']['measuredGroupCount']==12
    assert c['groupLeaveOneOut']['trendRMSEPixels']<1e-10
    assert c['groupLeaveOneOut']['improvementOverInterceptPixels']>.1
    assert not r['causeIdentified'] and not r['cameraParametersModified']
    assert r['observationCount']==12


def test_evaluation_residual_never_enters_fit_or_leave_group_out():
    payload=scene();first=module.diagnose(payload)
    camera={**payload['cameras'][0],'frameIndex':1};payload['cameras'].append(camera)
    for track in payload['tracks']:
        track['observations'].append({'frameIndex':1,'pixel':[5.,5.]})
    second=module.diagnose(payload)
    assert first['cameraReports']==second['cameraReports']
    evals=[r for r in second['allObservations'] if r['partition']=='evaluation']
    assert len(evals)==12 and not any(r['usedInFit'] for r in evals)
    payload['evaluation_indices']=[0,1]
    with pytest.raises(ValueError,match='partition_overlap'):module.diagnose(payload)


def test_degenerate_row_covariates_and_invalid_depth_remain_in_denominator():
    payload=scene()
    for track in payload['tracks']:
        track['worldPoint'][1]=0.
    payload['tracks'].append({'id':'behind','worldPoint':[0.,0.,-1.],
                              'observations':[{'frameIndex':0,'pixel':[480.,360.]}]})
    r=module.diagnose(payload)
    assert r['observationCount']==13
    assert r['reasonCounts']['non_positive_camera_depth']==1
    assert r['cameraReports'][0]['reason']=='insufficient_or_degenerate_training_observations'
    assert not any(x['usedInFit'] for x in r['allObservations'])


def test_eight_points_fit_but_failed_validation_folds_have_no_reported_gain():
    payload=scene();payload['tracks']=payload['tracks'][:8]
    report=module.diagnose(payload)['cameraReports'][0]
    assert report['reason']=='training_covariate_trend_only'
    validation=report['groupLeaveOneOut']
    assert validation['allGroupCount']==8 and validation['measuredGroupCount']==0
    assert not validation['fullyMeasured'] and 'improvementPixels' not in validation
