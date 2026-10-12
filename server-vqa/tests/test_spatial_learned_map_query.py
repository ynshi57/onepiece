import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy/spatial/research'))
from learned_map_query import align_sensor,associate

def fixture():
    a=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[1,1,1]],float)
    raw=np.tile(np.eye(4),(5,1,1));raw[:,:3,3]=2*a+[.2,.3,.4]
    return a,np.tile(np.eye(3),(5,1,1)),raw

def test_exact_similarity_preserves_metric_convention():
    a,r,raw=fixture();R,s,t,keep,angles,errors=align_sensor(a,r,raw)
    assert s==pytest.approx(2);assert np.allclose(t,[.2,.3,.4]);assert keep.all();assert np.max(errors)<1e-12

def test_wrong_quarter_turn_registration_rejected_without_disappearing():
    a,r,raw=fixture();r[-1]=np.array([[0,-1,0],[1,0,0],[0,0,1]])
    R,s,t,keep,angles,errors=align_sensor(a,r,raw)
    assert keep.tolist()==[True,True,True,True,False];assert angles[-1]==pytest.approx(90)

def test_zero_baseline_rejected():
    a,r,raw=fixture()
    with pytest.raises(ValueError,match='degenerate_center_baseline'):align_sensor(np.zeros_like(a),r,raw)

def test_query_conflicting_tracks_never_majority_voted_away():
    matches,bad=associate({0:[(1,10),(1,11),(2,12)],1:[(3,10),(3,10),(3,11)]},{1:1,2:2,3:3})
    assert len(bad)==1 and bad[0]['query']==0
    assert matches[0]['supportingTrainFrames']==[10,11]
