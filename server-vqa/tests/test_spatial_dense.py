"""Analytic photometric rejection tests; not real-world geometry accuracy claims."""
from pathlib import Path
import json
import numpy as np
from PIL import Image
from app.spatial_dense import _box_sum, _ncc, _warp, reconstruct_dense
from app.spatial_reconstruction import camera, resize_intrinsics


def test_resized_projection_matches_pixel_center_mapping():
    k = np.array([[950., 0., 959.5], [0., 940., 539.5], [0., 0., 1.]])
    ray = np.array([.3, -.2, 2.])
    original = k @ ray; original = original[:2] / original[2]
    resized = resize_intrinsics(k, 1920, 1080, 128, 72) @ ray
    np.testing.assert_allclose(resized[:2] / resized[2], (original + .5) / 15 - .5)
    np.testing.assert_allclose(k[0], [950., 0., 959.5])


def scene(directory, textured=False):
    rng=np.random.default_rng(10)
    texture=rng.integers(0,256,(96,180,3),dtype=np.uint8) if textured else np.full((96,180,3),220,dtype=np.uint8)
    frames=[]
    for i in range(5):
        t=np.eye(4); t[0,3]=i*.08
        name=f'frame-{i}.png'
        Image.fromarray(texture[:,i*4:i*4+128]).save(directory/name)
        frames.append({'time':float(i),'imageFile':name,'width':128,'height':96,
                       'intrinsics':[100.,0,0,0,100.,0,64.,48.,1.],
                       'cameraToWorld':t.ravel(order='F').tolist(),
                       'pose':{'time':float(i),'epoch':1,'position':dict(x=i*.08,y=0.,z=0.),'forward':dict(x=0.,y=0.,z=-1.)}})
    return {'keyframes':frames}


def test_box_sum_and_constant_patch_rejection():
    source=np.ones((7,8))
    assert _box_sum(source).shape==source.shape
    assert _box_sum(source)[3,3]==25
    cost,support=_ncc(source,np.ones((3,7,8)),np.ones((3,7,8),dtype=bool))
    assert np.isinf(cost).all() and not support.any()


def test_white_wall_is_unknown_not_fake_depth(tmp_path):
    result=reconstruct_dense(scene(tmp_path),tmp_path,maximum_frames=2,depth_count=16)
    assert not result['points']


def test_selection_experiment_keeps_same_holdout_and_unknown_denominator(tmp_path):
    document=scene(tmp_path)
    temporal=reconstruct_dense(document,tmp_path,maximum_frames=2,depth_count=16)
    overlap=reconstruct_dense(document,tmp_path,maximum_frames=2,depth_count=16,view_selection='pose_overlap')
    assert [f['heldoutFrameIndex'] for f in temporal['depthFrames']] == [f['heldoutFrameIndex'] for f in overlap['depthFrames']]
    assert temporal['summary']['evaluatedPixelCount'] == overlap['summary']['evaluatedPixelCount'] > 0
    assert overlap['summary']['unknownPixelCount'] == overlap['summary']['evaluatedPixelCount']
    for frame in overlap['depthFrames']:
        assert frame['heldoutFrameIndex'] not in frame['trainingFrameIndices']
        assert all(v['frameIndex'] != frame['heldoutFrameIndex'] for v in frame['viewCandidates'])


def test_unprocessable_views_remain_unknown_in_pixel_denominator(tmp_path):
    document=scene(tmp_path)
    document['keyframes']=document['keyframes'][:2]
    result=reconstruct_dense(document,tmp_path,maximum_frames=2,depth_count=16)
    assert result['summary']['processedFrameCount']==0
    assert result['summary']['evaluatedPixelCount']==2*128*96
    assert result['summary']['unknownPixelCount']==2*128*96
    assert result['summary']['unprocessedPixelCount']==2*128*96
    assert result['status']=='insufficient_evidence'
    assert all(v is None for f in result['depthFrames'] for row in f['depthMeters'] for v in row)
    json.dumps(result,allow_nan=False)


def test_warp_known_two_meter_plane(tmp_path):
    doc=scene(tmp_path,True)
    a,b=camera(doc['keyframes'][0]),camera(doc['keyframes'][1])
    yy,xx=np.mgrid[:96,:128]
    rays=np.linalg.solve(a.k,np.stack([xx,yy,np.ones_like(xx)]).reshape(3,-1)).reshape(3,96,128)
    target=np.asarray(Image.open(tmp_path/'frame-1.png'),dtype=float).mean(axis=2)
    warped,valid=_warp(a,b,rays,np.array([2.]),target)
    reference=np.asarray(Image.open(tmp_path/'frame-0.png'),dtype=float).mean(axis=2)
    assert np.allclose(warped[0,3:-3,8:-8],reference[3:-3,8:-8])
    assert valid.shape==(1,96,128)


def test_no_three_view_baseline_is_explicit(tmp_path):
    doc=scene(tmp_path)
    doc['keyframes']=doc['keyframes'][:2]
    result=reconstruct_dense(doc,tmp_path,maximum_frames=2,depth_count=16)
    assert result['summary']['processedFrameCount']==0
    assert len(result['frameDiagnostics'])==2
    assert all(d['reason']=='insufficient_three_view_baseline' for d in result['frameDiagnostics'])


def test_holdout_image_rejects_depth_without_changing_training_selection(tmp_path):
    doc=scene(tmp_path,True)
    first=reconstruct_dense(doc,tmp_path,maximum_frames=1,depth_count=48)
    assert first['depthFrames'][0]['trainingFrameIndices']==[1,2]
    assert first['depthFrames'][0]['heldoutFrameIndex']==3
    assert first['depthFrames'][0]['trainingCandidateCount']>0
    Image.fromarray(np.full((96,128,3),220,dtype=np.uint8)).save(tmp_path/'frame-3.png')
    second=reconstruct_dense(doc,tmp_path,maximum_frames=1,depth_count=48)
    assert second['depthFrames'][0]['trainingCandidateCount']==first['depthFrames'][0]['trainingCandidateCount']
    assert second['depthFrames'][0]['heldoutPassCount']==0
    assert second['depthFrames'][0]['heldoutFailCount']==second['depthFrames'][0]['trainingCandidateCount']
