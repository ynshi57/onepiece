"""Analytic plane tests, not a claim of real-world reconstruction accuracy."""
import numpy as np
from app.spatial_structure import recover_structure


def scene():
    # Two separated textured regions on a plane: the middle stays unknown.
    points=[]
    for i,x in enumerate(np.r_[np.linspace(0,.4,10),np.linspace(1,1.4,10)]):
        for j,y in enumerate(np.linspace(0,.4,10)):
            points.append({'id':f'p-{i}-{j}','epoch':1,'verifiedInThirdView':True,
                           'position':dict(x=float(x),y=float(y),z=-2.)})
    return points,[{'id':'old-order-0','epoch':1,'supportPointIDs':[p['id'] for p in points]}]


def test_unknown_gap_not_closed_and_no_wall_claim():
    points,candidates=scene()
    result=recover_structure(points,candidates)
    plane=result['planes'][0]
    assert result['wallCount']==0 and plane['semanticLabel'] is None
    assert plane['unknownCellCountWithinExtent']>0
    assert not plane['completeSurface'] and not plane['physicalBoundaryVerified']
    assert all(not c['surfaceRecovered'] for c in plane['supportCells'])
    assert all(e['kind']=='observed_support_to_unknown' for e in plane['supportEdges'])


def test_identity_stable_for_same_evidence_reordered():
    points,candidates=scene()
    first=recover_structure(points,candidates)['planes'][0]
    candidates[0]['id']='reordered-ransac-8'
    candidates[0]['supportPointIDs'].reverse()
    second=recover_structure(points[::-1],candidates)['planes'][0]
    assert first['id']==second['id']
    assert first['normal']==second['normal']
    assert first['supportCells']==second['supportCells']


def test_two_view_points_never_establish_structure():
    points,candidates=scene()
    for p in points: p['verifiedInThirdView']=False
    result=recover_structure(points,candidates)
    assert result['planes']==[]
    assert result['diagnostics'][0]['reason']=='no_verified_support'


def test_nonplanar_holdout_failure_is_retained():
    points,candidates=scene()
    # Alternating depth makes a plane impossible to support at the 3cm criterion.
    for i,p in enumerate(points): p['position']['z']+=.16*(i%2)
    result=recover_structure(points,candidates)
    assert result['planes']==[]
    assert result['diagnostics'][0]['validationFailureCells']>0


def test_epoch_mixing_is_rejected():
    points,candidates=scene()
    candidates[0]['epoch']=2
    assert not recover_structure(points,candidates)['planes']
