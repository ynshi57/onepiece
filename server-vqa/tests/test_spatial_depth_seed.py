"""Training/eval isolation and mathematical prior checks, not model geometry acceptance."""
import numpy as np
import pytest
from app.spatial_depth_seed import backproject,select_training_prior_frames,build_training_seeds,joint_training_calibration,spatial_joint_training_calibration,inverse_at,infer_heldout_pose_corrections
from app.spatial_reconstruction import Camera,project


def test_backprojection_inverts_cv_world_transform_not_arkit_axes():
    transform=np.diag([1.,-1.,-1.,1.]);transform[:3,3]=[.2,.3,.4]
    cam=Camera(np.array([[100.,0,50],[0,100.,50],[0,0,1.]]),np.eye(4),transform,3,0,100,100)
    point=backproject(cam,[60.,40.],2.)
    pixel,z=project(cam,point)
    assert np.allclose(pixel,[60.,40.]) and z==pytest.approx(2.)
    with pytest.raises(ValueError,match='nonpositive'):backproject(cam,[50,50],-2.)


def test_frame_selection_is_training_only_and_preinference():
    anchors=[{'frameIndex':i,'trainingSupportFrames':[i,(i+1)%10]} for i in range(10)]
    selected=select_training_prior_frames(anchors,list(range(10)),8)
    assert selected['frameIndices']==list(range(8))
    assert selected['selectionPolicy']=='training_anchor_count_before_prediction'
    with pytest.raises(ValueError,match='evaluation_anchor'):select_training_prior_frames(anchors,list(range(9)),8)


def test_eval_depth_cannot_become_seed_even_if_numeric_valid(tmp_path):
    with pytest.raises(ValueError,match='evaluation_depth_forbidden'):
        build_training_seeds({'keyframes':[]},tmp_path,{7:np.ones((100,100))},[],[0,1],epoch=3)


def test_seed_provenance_and_failed_view_denominators(monkeypatch,tmp_path):
    from PIL import Image
    import app.spatial_depth_seed as seed
    cam=Camera(np.array([[100.,0,50],[0,100.,50],[0,0,1.]]),np.eye(4),np.eye(4),3,0,100,100)
    monkeypatch.setattr(seed,'camera',lambda frame:cam)
    def fit(depth,anchors,*,frame_index,training_frame_indices):
        if frame_index==1:raise ValueError('spatially_local_training_anchors')
        return {'a':1.,'b':0.}
    monkeypatch.setattr(seed,'fit_inverse_depth',fit)
    Image.new('RGB',(100,100),(90,100,110)).save(tmp_path/'image.png')
    document={'keyframes':[{'pose':{'epoch':3},'imageFile':'image.png'}]*2}
    result=seed.build_training_seeds(document,tmp_path,{0:np.ones((100,100)),1:np.ones((100,100))},[],[0,1],epoch=3,stride=32)
    assert result['pointCount']==9 and result['candidatePointCount']==18 and result['rejectedPointCount']==9
    assert result['selectedViewCount']==2 and result['successfulViewCount']==1
    assert result['views'][1]['reason']=='spatially_local_training_anchors'
    assert all(p['source']=='model_prior' and p['verifiedSurface'] is False for p in result['points'])


def test_heldout_denominator_keeps_reprojection_failure_no_source_and_empty_frame(monkeypatch,tmp_path):
    import app.spatial_depth_seed as seed
    cam=Camera(np.array([[100.,0,50],[0,100.,50],[0,0,1.]]),np.eye(4),np.eye(4),3,0,100,100)
    monkeypatch.setattr(seed,'camera',lambda frame:cam)
    observations=lambda source,target:[{'frameIndex':source,'pixel':[50,50]},{'frameIndex':2,'pixel':target}]
    monkeypatch.setattr(seed,'reconstruct',lambda document,directory:{'featureTracks':[
        {'id':'pass','observations':observations(0,[50,50])},
        {'id':'fail','observations':observations(0,[60,50])},
        {'id':'no-source','observations':observations(1,[45,45])}]})
    result=seed.validate_heldout({'keyframes':[{'pose':{'epoch':3}}]*4},tmp_path,
        {'trainingFrameIndices':[0,1],'epoch':3,'points':[{'position':{'x':0.,'y':0.,'z':1.}}],
         'calibrations':{0:{'a':1.,'b':0.}}},{0:np.ones((100,100))},[2,3])
    assert result['candidateCount']==3 and result['passedReprojectionCount']==1 and result['failedCount']==2
    assert result['evaluationFrameCount']==2 and result['frames'][1]['status']=='failed_no_feature_track_observations'
    assert result['allProjectedMedianErrorPixels']==pytest.approx(5.)
    assert result['frames'][0]['reasonCounts']['no_selected_training_prior_observation']==1
    assert 'points' not in result and result['evaluationUsedForFittingOrSeeds'] is False
    frozen=[{'id':'fixed','observations':observations(0,[50,50])}]
    frozen_result=seed.validate_heldout({'keyframes':[{'pose':{'epoch':3}}]*4},tmp_path,
        {'trainingFrameIndices':[0,1],'epoch':3,'points':[{'position':{'x':0.,'y':0.,'z':1.}}],
         'calibrations':{0:{'a':1.,'b':0.}}},{0:np.ones((100,100))},[2,3],
        frozen_validation_tracks=frozen,frozen_validation_indices=[0,1,2,3])
    assert frozen_result['candidateCount']==1
    with pytest.raises(ValueError,match='validation_frame_indices_changed'):
        seed.validate_heldout({'keyframes':[{'pose':{'epoch':3}}]*4},tmp_path,
            {'trainingFrameIndices':[0,1],'epoch':3,'points':[],'calibrations':{}},
            {0:np.ones((100,100))},[2,3],frozen_validation_tracks=frozen,
            frozen_validation_indices=[0,1,3,2])
    with pytest.raises(ValueError,match='overlap'):
        seed.validate_heldout({'keyframes':[]},tmp_path,{'trainingFrameIndices':[0]}, {},[0])


def test_joint_calibration_uses_training_tracks_and_preserves_positive_depth(monkeypatch):
    import app.spatial_depth_seed as seed
    k=np.array([[100.,0,50],[0,100.,50],[0,0,1.]])
    cams={}
    for i in range(6):
        w2c=np.eye(4);w2c[0,3]=-.1*i
        cams[i]=Camera(k,np.linalg.inv(w2c),w2c,3,0,100,100)
    monkeypatch.setattr(seed,'camera',lambda frame:cams[frame['index']])
    doc={'keyframes':[{'index':i} for i in range(6)]}
    depths={i:np.ones((100,100)) for i in range(6)}
    fit={i:{'a':1.,'b':0.} for i in range(6)}
    tracks=[]
    for j,x in enumerate((-.1,0.,.1)):
        tracks.append({'id':str(j),'observations':[
            {'frameIndex':i,'pixel':[50+100*(x-.1*i)/2,50]} for i in range(6)]})
    result=joint_training_calibration(doc,depths,{'calibrations':fit,'epoch':3},tracks,list(range(6)))
    assert result['trainingPairCount']==90
    assert result['optimizedTrainingObjective']<result['initialTrainingObjective']
    assert result['optimizedNonprojectableCount']==0
    assert all(f['a']+f['b']>.05 for f in result['calibrations'].values())
    assert result['evaluationUsed'] is False and result['scaleVerified'] is False
    leaked=tracks+[{'id':'eval','observations':[{'frameIndex':6,'pixel':[50,50]}]}]
    with pytest.raises(ValueError,match='evaluation_track_forbidden'):
        joint_training_calibration(doc,depths,{'calibrations':fit,'epoch':3},leaked,list(range(6)))
    with pytest.raises(ValueError,match='insufficient_spatial_training_anchors'):
        spatial_joint_training_calibration(doc,depths,{'calibrations':fit,'epoch':3},tracks,list(range(6)))
    for f in fit.values():f['anchorCount']=30
    spatial=spatial_joint_training_calibration(doc,depths,{'calibrations':fit,'epoch':3},tracks,list(range(6)),iterations=2)
    assert spatial['spatialTerms'] is True and spatial['optimizedTrainingObjective']<spatial['initialTrainingObjective']
    assert spatial['optimizedNonprojectableCount']==0
    sample={'a':1.,'b':.5,'c':.2,'d':-.1}
    assert inverse_at(sample,2.,[100,0],cams[0])==pytest.approx(2.65)


def test_eval_pose_correction_interpolates_training_deltas_without_eval_features():
    frames=[]
    for i in range(5):
        frames.append({'intrinsics':np.array([[100.,0,50],[0,100.,50],[0,0,1.]]).ravel(order='F').tolist(),
            'cameraToWorld':np.eye(4).ravel(order='F').tolist(),'time':float(i),'width':100,'height':100,
            'pose':{'epoch':3,'time':float(i),'position':{'x':0.,'y':0.,'z':0.},
                    'forward':{'x':0.,'y':0.,'z':-1.}}})
    records=[]
    for local,i in enumerate((0,2,4)):
        t=np.eye(4);t[0,3]=.02*(local+1)
        records.append({'frameIndex':local,'heldout':False,'cameraToWorld':t.ravel(order='F').tolist()})
    ba={'summary':{'heldoutFrameIndices':[]},'cameraFrames':records}
    result=infer_heldout_pose_corrections({'keyframes':frames},ba,[0,2,4],[1,3])
    assert result['status']=='inferred_pose_correction_not_ground_truth'
    assert result['evaluationRGBOrFeaturesUsedForPose'] is False
    assert result['document']['keyframes'][1]['pose']['position']['x']==pytest.approx(.03)
    assert result['document']['keyframes'][3]['pose']['position']['x']==pytest.approx(.05)
    assert result['document']['keyframes'][0]==frames[0]
    assert frames[1]['pose']['position']['x']==0.
