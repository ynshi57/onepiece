import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy/spatial/research'))
from learned_query_diagnosis import project,raw_ar_in_map,summary

def test_ar_to_map_projection_same_as_world_projection():
    theta=.4;R=np.array([[np.cos(theta),0,np.sin(theta)],[0,1,0],[-np.sin(theta),0,np.cos(theta)]]);s=2.;t=np.array([1.,2.,3.]);mapx=np.array([.2,.4,5.]);raw=np.eye(4);raw[:3,:3]=np.diag([1,-1,-1]);raw[:3,3]=[.1,.2,.3];f={'cameraToWorld':raw.ravel(order='F').tolist()};a={'rotation':R.tolist(),'translation':t.tolist(),'scaleToAR':s};k=np.array([[600,0,320],[0,610,240],[0,0,1.]])
    ar=np.linalg.inv(raw@np.diag([1,-1,-1,1.]));worldx=s*R@mapx+t;expected=project(worldx,ar,k,np.array([0,0.]));actual=project(mapx,raw_ar_in_map(f,a),k,np.array([0,0.]));assert np.allclose(actual['predictedPixel'],expected['predictedPixel'])

def test_all_attempt_denominator_keeps_negative_depth():
    k=np.eye(3);good=project([0,0,1],np.eye(4),k,[0,0]);bad=project([0,0,-1],np.eye(4),k,[0,0]);r=summary([{'p':good},{'p':bad}],'p');assert r['attemptCount']==2;assert r['passedFractionAllAttempts']==.5;assert r['reasonCounts']['negative_depth']==1
