"""Analytic isolation/gauge/optimization tests; real-camera evaluation is separate."""
from copy import deepcopy
import json
import numpy as np
from app.spatial_bundle_adjustment import bundle_adjust, _prediction
from app.spatial_reconstruction import camera, project


def fixture():
    frames=[]
    for i in range(8):
        transform=np.eye(4); transform[0,3]=i*.07
        frames.append({'time':float(i),'width':640,'height':480,'intrinsics':[500.,0,0,0,500.,0,320.,240.,1.],
                       'cameraToWorld':transform.ravel(order='F').tolist(),
                       'pose':{'time':float(i),'epoch':1,'position':dict(x=i*.07,y=0.,z=0.),'forward':dict(x=0.,y=0.,z=-1.)}})
    cams=[camera(f) for f in frames]
    tracks=[]
    for i in range(24):
        xyz=np.array([-.4+(i%6)*.16,-.25+(i//6)*.15,-2.-.1*(i%3)])
        tracks.append({'id':f't-{i}','observations':[{'frameIndex':j,'pixel':project(c,xyz)[0].tolist(),'featureIndex':i} for j,c in enumerate(cams)]})
    # Realistic raw position measurement error; retain internally consistent metadata.
    for i in [1,3,4,5,7]:
        frames[i]['cameraToWorld'][12]+=.015*(-1 if i%2 else 1)
        frames[i]['pose']['position']['x']=frames[i]['cameraToWorld'][12]
    return {'keyframes':frames},tracks


def test_holdout_pixels_never_influence_optimized_geometry():
    doc,tracks=fixture()
    before=deepcopy(doc)
    a=bundle_adjust(doc,tracks,iterations=3)
    altered=deepcopy(tracks)
    for track in altered:
        track['observations'][6]['pixel'][0]+=120
    b=bundle_adjust(doc,altered,iterations=3)
    assert a['points']==b['points'] or all(p['position']==q['position'] for p,q in zip(a['points'],b['points']))
    assert a['cameraFrames']==b['cameraFrames']
    assert a['summary']['optimizedTrainingObjective']==b['summary']['optimizedTrainingObjective']
    assert a['summary']['optimizedHoldoutPassCount']!=b['summary']['optimizedHoldoutPassCount']
    assert doc==before
    assert a['summary']['heldoutFrameIndices']==[6]
    for frame in a['cameraFrames']:
        if frame['heldout'] or frame['anchor']:
            assert np.allclose(frame['cameraToWorld'],frame['originalCameraToWorld'])


def test_training_objective_decreases_but_never_declares_slam():
    doc,tracks=fixture()
    result=bundle_adjust(doc,tracks,iterations=4)
    assert result['summary']['optimizedTrainingObjective']<result['summary']['initialTrainingObjective']
    assert result['defaultEnabled'] is False
    assert result['scaleVerified'] is False
    assert result['status']=='experiment_only'
    json.dumps(result, allow_nan=False)


def test_capacity_and_uninitializable_tracks_retained():
    doc,tracks=fixture()
    tracks.append({'id':'cannot-initialize','observations':[{'frameIndex':6,'pixel':[1,2],'featureIndex':0}]})
    result=bundle_adjust(doc,tracks,iterations=1,maximum_tracks=5)
    assert result['summary']['capacityExcludedTrackCount']==19
    assert result['summary']['inputTrackCount']==25
    assert len(result['trackDiagnostics'])==25
    assert result['summary']['initializationRejectedReasons']['insufficient_observations']==1


def test_projection_jacobian_matches_finite_difference():
    doc,_=fixture(); cam=camera(doc['keyframes'][2]); point=np.array([.15,.12,-2.1])
    predicted,jpoint,_=_prediction(cam,np.eye(3),np.zeros(3),point)
    for axis in range(3):
        delta=np.zeros(3); delta[axis]=1e-6
        next_pixel=_prediction(cam,np.eye(3),np.zeros(3),point+delta)[0]
        assert np.allclose((next_pixel-predicted)/1e-6,jpoint[:,axis],atol=1e-4)


def test_explicit_thirteen_development_frames_never_enter_training():
    doc,tracks=fixture()
    # 88-frame partition with the existing development selection; no invented
    # visual observations are added for the unused extra cameras.
    doc['keyframes'] += [deepcopy(doc['keyframes'][-1]) for _ in range(80)]
    evaluation=[i for i in range(88) if i%7==3]
    training=[i for i in range(88) if i not in evaluation]
    assert len(evaluation)==13
    a=bundle_adjust(doc,tracks,iterations=2,training_indices=training,evaluation_indices=evaluation)
    changed=deepcopy(tracks)
    for t in changed:
        t['observations'][3]['pixel'][0]+=100
    b=bundle_adjust(doc,changed,iterations=2,training_indices=training,evaluation_indices=evaluation)
    assert a['cameraFrames']==b['cameraFrames']
    assert [p['position'] for p in a['points']]==[p['position'] for p in b['points']]
    assert a['summary']['optimizedTrainingObjective']==b['summary']['optimizedTrainingObjective']
    assert a['partition']['evaluationIndices']==evaluation
    assert a['partition']['source']=='explicit_indices'
    assert all(not(set(d['trainingFrames'])&set(evaluation)) for d in a['trackDiagnostics'])


def test_nonparticipating_cameras_are_not_optimized_or_anchors():
    doc,tracks=fixture()
    tracks=deepcopy(tracks)
    for t in tracks:
        t['observations']=[o for o in t['observations'] if o['frameIndex'] in {1,2,3}]
    result=bundle_adjust(doc,tracks,iterations=1,training_indices=[1,2],evaluation_indices=[3])
    assert result['partition']['nonparticipatingFrameIndices']==[0,4,5,6,7]
    assert {c['frameIndex'] for c in result['cameraFrames']}=={1,2,3}
    assert result['summary']['anchorFrameIndices']==[1]


def test_invalid_partitions_and_observation_groups_rejected():
    import pytest
    doc,tracks=fixture()
    for kwargs in [dict(training_indices=[0,1]),
                   dict(training_indices=[0,0],evaluation_indices=[3]),
                   dict(training_indices=[0,1],evaluation_indices=[1]),
                   dict(training_indices=[-1],evaluation_indices=[]),
                   dict(training_indices=[8],evaluation_indices=[]),
                   dict(training_indices=[True],evaluation_indices=[]),
                   dict(training_indices=[],evaluation_indices=[3])]:
        with pytest.raises(ValueError):bundle_adjust(doc,tracks,**kwargs)
    with pytest.raises(ValueError,match='outside_declared_partition'):
        bundle_adjust(doc,tracks,training_indices=[0,1],evaluation_indices=[3])
    repeated=deepcopy(tracks);repeated[0]['observations'].append(deepcopy(repeated[0]['observations'][0]))
    with pytest.raises(ValueError,match='duplicate_track_frame'):bundle_adjust(doc,repeated)
    with pytest.raises(ValueError,match='duplicate_track_identity'):bundle_adjust(doc,tracks+[tracks[0]])
    bad=deepcopy(tracks);bad[0]['observations'][0]['frameIndex']=99
    with pytest.raises(ValueError,match='invalid_observation'):bundle_adjust(doc,bad)
