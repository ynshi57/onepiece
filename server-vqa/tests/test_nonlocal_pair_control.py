import importlib.util
from pathlib import Path
import sys
import numpy as np

research=Path(__file__).resolve().parents[2]/'deploy/spatial/research'
sys.path.insert(0,str(research))
spec=importlib.util.spec_from_file_location('nonlocal_pair_control',research/'nonlocal_pair_control.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def frame(time, x=0., epoch=2, reverse=False):
    t=np.eye(4);t[0,3]=x
    if reverse:t[:3,:3]=np.diag([-1,1,-1])
    return dict(time=time,pose=dict(epoch=epoch),cameraToWorld=t.ravel(order='F').tolist())


def test_proposals_exclude_existing_near_time_and_other_epochs():
    f=[frame(0),frame(5),frame(20),frame(30,epoch=3)]
    assert m.propose(f,[0,1,2,3],[])==[(0,2),(1,2)]
    assert m.propose(f,[0,1,2,3],[(2,0),(1,2)])==[]


def test_far_or_opposite_facing_views_are_not_proposed():
    assert m.propose([frame(0),frame(20,x=2),frame(40,reverse=True)],[0,1,2],[])==[]


def test_pair_proposals_are_unique_and_budgeted():
    f=[frame(i*20,x=i*.01) for i in range(10)]
    p=m.propose(f,list(range(10)),[],neighbors=1)
    assert len(p)==len(set(p)) and len(p)<=10
    assert all(a<b for a,b in p)
