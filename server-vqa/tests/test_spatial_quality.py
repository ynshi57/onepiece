"""Protocol consistency tests; do not claim real-world drift ground truth."""
from app.spatial_quality import diagnose


def pose(t,x=0,epoch=1,forward=(0,0,-1)):
    return {'time':t,'epoch':epoch,'position':dict(zip(('x','y','z'),(x,0,0))), 'forward':dict(zip(('x','y','z'),forward))}


def test_discontinuity_has_source_time_and_no_cross_epoch_speed():
    doc={'poses':[pose(100),pose(101,1),pose(102,100,epoch=2),pose(105,101,epoch=2),pose(104,102,epoch=2)]}
    r=diagnose(doc)
    assert r['eventCounts']=={'epoch_transition':1,'time_gap':1,'non_increasing_time':1}
    assert r['speedMetersPerSecond']['sampleCount']==1
    assert r['events'][0]['sourcePoseIndices']==[1,2]
    assert r['events'][0]['relativeStartSeconds']==1
    assert r['originalPreserved'] and not r['groundTruthDriftMeasured']
    assert doc['poses'][2]['position']['x']==100


def test_turn_translation_is_candidate_not_drift():
    r=diagnose({'poses':[pose(0),pose(.5,.3,forward=(1,0,0))]})
    assert r['eventCounts']=={'turn_with_translation_candidate':1}
    assert r['events'][0]['turnAngleDegrees']==90
    assert not r['groundTruthDriftMeasured']


def test_invalid_pose_breaks_measurement():
    r=diagnose({'poses':[pose(0),pose(1,float('nan')),pose(2,100)]})
    assert r['eventCounts']=={'invalid_pose':1}
    assert r['speedMetersPerSecond']['sampleCount']==0


def test_speed_anomaly_visible_and_included_in_statistics():
    r=diagnose({'poses':[pose(0),pose(.1,1)]})
    assert r['eventCounts']=={'speed_anomaly_candidate':1}
    assert r['speedMetersPerSecond']['maximum']==10
