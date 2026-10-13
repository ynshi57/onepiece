"""Controlled-data checks for diagnostics, not room reconstruction acceptance."""
import importlib.util
import json
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip('scipy', reason='optional --with-spatial-geometry research profile required')

spec=importlib.util.spec_from_file_location('hypotheses',Path(__file__).resolve().parents[2]/'deploy/spatial/research/pair_geometry_hypotheses.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def inputs(tmp_path):
    k=np.array([[500.,0,320.],[0,500.,240.],[0,0,1.]])
    frames=[]
    for i in range(6):
        t=np.diag([1.,-1.,-1.,1.]);t[0,3]=i*.1
        frames.append(dict(intrinsics=k.flatten(order='F').tolist(),cameraToWorld=t.flatten(order='F').tolist(),
            width=640,height=480,time=float(i),pose=dict(time=float(i),epoch=1,
            position=dict(x=i*.1,y=0.,z=0.),forward=dict(x=0.,y=0.,z=1.))))
    d=tmp_path/'document.json';d.write_text(json.dumps(dict(keyframes=frames)))
    records=[];rng=np.random.default_rng(17)
    xyz=np.column_stack((rng.uniform(-.5,.5,50),rng.uniform(-.6,.6,50),rng.uniform(2,5,50)))
    for i in range(5):
        ps=[]
        for j in (i,i+1):
            q=(xyz-[j*.1,0,0])@k.T;ps.append(q[:,:2]/q[:,2:])
        mask=(np.arange(50)%2==0).tolist()
        records.append(dict(frameA=i,frameB=i+1,status='measured',baselineARUnits=.1,
            pointsA=ps[0].tolist(),pointsB=ps[1].tolist(),fittingMask=mask,fittingCount=25,scoringCount=25))
    control=dict(inputDocumentSHA256=m.sha(d),trainingIndices=list(range(6)),evaluationExcluded=[],records=records)
    c=tmp_path/'control.json';c.write_text(json.dumps(control))
    return d,c,control


def test_calibration_uses_disjoint_camera_block_and_cannot_fit_score_changes(tmp_path):
    d,c,control=inputs(tmp_path);r=m.run(d,c,tmp_path/'first.json')
    assert [x['sharedKRole'] for x in r['records']]==['fit','gap','score','score','score']
    for row in control['records'][2:]:
        row['pointsB']=(np.array(row['pointsB'])+[0.,8.]).tolist()
    c.write_text(json.dumps(control));changed=m.run(d,c,tmp_path/'second.json')
    np.testing.assert_allclose(r['sharedKParameters'],changed['sharedKParameters'],atol=1e-12)
    assert r['requestedPairCount']==changed['requestedPairCount']==5
    assert changed['productAccepted'] is False
    assert changed['posesModified'] is False


def test_solver_failure_retains_original_baseline_and_all_records(tmp_path,monkeypatch):
    d,c,_=inputs(tmp_path)
    def fail(*args,**kwargs):raise ValueError('forced_solver_failure')
    monkeypatch.setattr(m,'least_squares',fail)
    r=m.run(d,c,tmp_path/'failed.json')
    assert not r['sharedKSolverSuccess']
    assert len(r['records'])==5
    assert all(x['status']=='solver_failed' and x['raw']['measuredCount']==25 for x in r['records'])
