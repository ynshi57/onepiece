import importlib.util
import sys
from pathlib import Path
import pytest
pytest.importorskip('scipy',reason='--with-spatial-geometry required')
pytest.importorskip('pycolmap',reason='--with-spatial-geometry required')
import numpy as np

research=Path(__file__).resolve().parents[2]/'deploy/spatial/research'
sys.path.insert(0,str(research))
spec=importlib.util.spec_from_file_location('native_ba_control',research/'native_ba_control.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_linear_control_preserves_fixed_gauge_and_score_landmark():
    r=np.repeat(np.eye(3)[None],3,axis=0);t=np.array([[0,0,0],[-.3,0,0],[-.6,0,0]])
    k=np.repeat(np.array([[500,500,320,240]],float),3,axis=0)
    p=np.array([[.1,.2,3],[.3,.1,4],[.1,-.2,5],[.4,.3,4]],float)
    ci=np.tile(np.arange(3),4);pi=np.repeat(np.arange(4),3);xy=m.project(p[pi],ci,r,t,k)[0]
    wrong=p.copy();wrong[0,0]+=.01
    pp,rr,tt,meta=m.scipy_l2(wrong,ci,pi,xy,r,t,k,[0,1,2],[0,2],max_evaluations=10)
    np.testing.assert_array_equal(pp[3],wrong[3]);np.testing.assert_array_equal(rr[[0,2]],r[[0,2]]);np.testing.assert_array_equal(tt[[0,2]],t[[0,2]])
    assert meta['finalSSE']<meta['initialSSE'];assert meta['finalBehindObservations']==0


def test_l2_objective_is_signed_depth_without_hidden_penalty():
    r=np.repeat(np.eye(3)[None],3,axis=0);t=np.array([[0,0,0],[-.2,0,0],[-.4,0,0]])
    k=np.repeat(np.array([[500,500,320,240]],float),3,axis=0);p=np.array([[.1,.2,-3]])
    ci=np.arange(3);pi=np.zeros(3,dtype=int);xy=m.project(p[pi],ci,r,t,k)[0];xy[0,0]+=2
    _,_,_,meta=m.scipy_l2(p,ci,pi,xy,r,t,k,[0],[0,2],max_evaluations=1)
    assert meta['initialSSE']==4 and meta['finalBehindObservations']==3
    assert meta['accepted'] is False
