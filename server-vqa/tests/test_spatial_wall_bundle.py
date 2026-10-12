"""Analytic geometry tests; actual scene evidence is recorded separately, not mocked acceptance."""
import numpy as np
import pytest
from app.spatial_reconstruction import Camera, project
from app.spatial_wall_bundle import fit_vertical_wall_pixels

def scene():
    k=np.array([[700.,0,480],[0,700,360],[0,0,1.]])
    cams={}
    for i,x in enumerate((-.25,0,.25)):
        cv=np.eye(4);cv[:3,3]=[-x,0,0];c2w=np.linalg.inv(cv)@np.diag([1.,-1.,-1.,1.])
        cams[i]=Camera(k,c2w,cv,0,float(i),960,720)
    tracks=[]
    for j,(x,y) in enumerate(((-.4,-.2),(.1,.4),(.5,-.1),(-.1,.1))):
        xyz=np.array([x,y,3.]);obs=[]
        for i,c in cams.items():
            pixel,_=project(c,xyz);pixel=pixel+np.array([.15*(-1)**(j+i),.08*(-1)**i])
            obs.append(dict(frameIndex=i,pixel=pixel.tolist()))
        tracks.append(dict(id=str(j),observations=obs))
    return cams,tracks

def test_plane_pixel_fit_keeps_all_observations_and_does_not_claim_wall():
    cams,tracks=scene();r=fit_vertical_wall_pixels([0,0,1],-2.9,tracks,cams,component_id=0,training_indices=[0,1,2],evaluation_indices=[3])
    assert r['observationCount']==12
    assert r['finalRobustPixelObjective']<r['initialRobustPixelObjective']
    assert abs(r['offset']+3)<.03
    assert r['p95ResidualPixels']<1
    assert r['normal'][1]==0
    assert not r['completeSurface'] and not r['productAccepted']

def test_repeated_view_cannot_count_as_independent_observation():
    cams,tracks=scene();tracks[0]['observations'][2]=tracks[0]['observations'][1]
    with pytest.raises(ValueError,match='distinct_training_views'):fit_vertical_wall_pixels([0,0,1],-3,tracks,cams,component_id=0,training_indices=[0,1,2],evaluation_indices=[3])

def test_camera_from_another_map_epoch_is_rejected():
    cams,tracks=scene();c=cams[2];cams[2]=Camera(c.k,c.camera_to_world,c.world_to_camera,1,c.time,c.width,c.height)
    with pytest.raises(ValueError,match='mixed_map_epochs'):fit_vertical_wall_pixels([0,0,1],-3,tracks,cams,component_id=0,training_indices=[0,1,2],evaluation_indices=[3])

def test_anchor_flags_and_nonanchor_metrics_exclude_constructed_zero_residuals():
    cams,tracks=scene()
    r=fit_vertical_wall_pixels([0,0,1],-2.9,tracks,cams,component_id=0,
                               training_indices=[0,1,2],evaluation_indices=[3])
    anchors=[x for x in r['allObservations'] if x['isAnchor']]
    others=[x for x in r['allObservations'] if not x['isAnchor']]
    assert len(anchors)==4 and len(others)==8
    assert max(x['residualPixels'] for x in anchors)<1e-8
    assert r['nonAnchorObservationCount']==8
    assert r['nonAnchorMedianResidualPixels']==pytest.approx(np.median([x['residualPixels'] for x in others]))
    assert r['maximumCameraBaseline']==pytest.approx(.5)
    assert min(r['finalJacobianSingularValues'])>0
    assert len(r['frozenInputSHA256'])==64
    assert not r['independentWallAccuracyVerified']


def test_partition_overlap_and_evaluation_observation_are_rejected_before_fit():
    cams,tracks=scene()
    with pytest.raises(ValueError,match='partition_overlap'):
        fit_vertical_wall_pixels([0,0,1],-3,tracks,cams,component_id=0,
                                 training_indices=[0,1,2],evaluation_indices=[2,3])
    tracks[0]['observations'][2]['frameIndex']=3
    with pytest.raises(ValueError,match='observation_outside_training_partition'):
        fit_vertical_wall_pixels([0,0,1],-3,tracks,cams,component_id=0,
                                 training_indices=[0,1,2],evaluation_indices=[3])


def test_pure_rotation_no_translation_retains_unobservable_failure_evidence():
    from app.spatial_wall_bundle import WallBundleFailure
    cams,tracks=scene()
    reference=cams[0]
    for index,c in list(cams.items()):
        cams[index]=Camera(c.k,reference.camera_to_world,reference.world_to_camera,
                           c.epoch,c.time,c.width,c.height)
    with pytest.raises(WallBundleFailure,match='zero_translation_baseline') as caught:
        fit_vertical_wall_pixels([0,0,1],-3,tracks,cams,component_id=0,
                                 training_indices=[0,1,2],evaluation_indices=[3])
    evidence=caught.value.evidence
    assert evidence['maximumCameraBaseline']==0
    assert len(evidence['trackRayAngles'])==4
    assert evidence['trainingIndices']==[0,1,2]
    assert not evidence['productAccepted']
    assert len(evidence['frozenInputSHA256'])==64


def test_frozen_digest_changes_when_observed_pixels_change():
    cams,tracks=scene()
    kwargs=dict(component_id=0,training_indices=[0,1,2],evaluation_indices=[3])
    first=fit_vertical_wall_pixels([0,0,1],-3,tracks,cams,**kwargs)
    tracks[0]['observations'][1]['pixel'][0]+=.2
    second=fit_vertical_wall_pixels([0,0,1],-3,tracks,cams,**kwargs)
    assert first['frozenInputSHA256']!=second['frozenInputSHA256']
