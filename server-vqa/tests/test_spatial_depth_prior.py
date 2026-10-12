"""Integrity and training-only fit checks; numerical fixtures are not model evaluation."""
import hashlib
import json
import shutil
import numpy as np
import pytest
from app import spatial_depth_prior as prior


def fixture(local=False,negative=False):
    y,x=np.mgrid[:100,:100];depth=1+x/100+y/100
    axis=[10,12,14,16] if local else [10,35,60,85]
    anchors=[]
    for yy in axis:
        for xx in axis:
            inv=3-.2*depth[yy,xx] if negative else .1*depth[yy,xx]+.2
            anchors.append({'frameIndex':2,'pixel':[xx,yy],'depthMeters':1/inv,
                'trainingSupportFrames':[2,4],'source':'training_image_track_retriangulation'})
    return depth,anchors


def test_fixed_official_source_manifest_and_every_git_blob_verify():
    manifest=prior.verify_source(prior.DEFAULT_SOURCE_ROOT)
    assert manifest['revision']==prior.SOURCE_COMMIT
    assert len(manifest['files'])==13


def test_tampered_blob_and_forged_manifest_rejected(tmp_path):
    source=tmp_path/'source';shutil.copytree(prior.DEFAULT_SOURCE_ROOT,source)
    blob=source/'upstream/depth_anything_v2/dpt.py';blob.write_bytes(blob.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='blob_mismatch'):prior.verify_source(source)
    manifest=json.loads((source/'source-manifest.json').read_text())
    data=blob.read_bytes()
    for item in manifest['files']:
        if item['path']=='depth_anything_v2/dpt.py':
            item['size']=len(data);item['sha']=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    (source/'source-manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='untrusted_depth_source_manifest'):prior.verify_source(source)


def test_weight_size_and_sha_are_both_required(tmp_path,monkeypatch):
    path=tmp_path/'weight';path.write_bytes(b'correct')
    monkeypatch.setattr(prior,'WEIGHTS_BYTES',7)
    monkeypatch.setattr(prior,'WEIGHTS_SHA256',hashlib.sha256(b'correct').hexdigest())
    assert prior.verify_weights(path)==prior.WEIGHTS_SHA256
    path.write_bytes(b'incorrc')
    with pytest.raises(ValueError,match='sha256'):prior.verify_weights(path)
    path.write_bytes(b'x')
    with pytest.raises(ValueError,match='size'):prior.verify_weights(path)


def test_positive_inverse_fit_is_training_only_not_measured_geometry():
    depth,anchors=fixture();fit=prior.fit_inverse_depth(depth,anchors,frame_index=2,training_frame_indices=[2,4])
    assert fit['a']==pytest.approx(.1) and fit['b']==pytest.approx(.2)
    assert fit['evaluationUsed'] is False and fit['confidenceAvailable'] is False
    assert fit['status']=='fitted_training_prior_not_measured_geometry'
    with pytest.raises(ValueError,match='evaluation_frame_forbidden'):
        prior.fit_inverse_depth(depth,anchors,frame_index=2,training_frame_indices=[4])
    anchors[0]['trainingSupportFrames'].append(7)
    with pytest.raises(ValueError,match='non_training_anchor_forbidden'):
        prior.fit_inverse_depth(depth,anchors,frame_index=2,training_frame_indices=[2,4])


@pytest.mark.parametrize('kind,reason',[('constant','relative_span'),('negative','nonpositive_depth_scale'),('local','spatially_local')])
def test_degenerate_priors_are_rejected(kind,reason):
    depth,anchors=fixture(local=kind=='local',negative=kind=='negative')
    if kind=='constant':depth[:]=1
    with pytest.raises(ValueError,match=reason):
        prior.fit_inverse_depth(depth,anchors,frame_index=2,training_frame_indices=[2,4])


def test_display_color_does_not_change_raw_relative_units():
    depth=np.array([[0.,1.],[2.,3.]],dtype=np.float32);before=depth.copy()
    rgb=prior.color_relative(depth)
    assert rgb.shape==(2,2,3) and rgb.dtype==np.uint8
    assert np.array_equal(before,depth)


def test_anchor_retriangulation_uses_preverification_tracks_and_keeps_failures(monkeypatch,tmp_path):
    import app.spatial_reconstruction as reconstruction
    frames=[{'pose':{'epoch':2},'originalIndex':i} for i in range(3)]
    seen=[]
    def seed(document,directory):
        seen.extend(document['keyframes'])
        return {'points':[{'verifiedInThirdView':True,'position':{'x':999,'y':999,'z':999}}],
            'featureTracks':[{'id':'raw','observations':[{'frameIndex':0,'pixel':[5,5]},{'frameIndex':1,'pixel':[6,5]}]},
                             {'id':'failed','observations':[{'frameIndex':0,'pixel':[0,0]}]}]}
    def triangulate(cameras,pixels):
        if len(cameras)<2:raise ValueError('insufficient_observations')
        return None,None,[2.,2.1],[.2,.3],3.
    monkeypatch.setattr(reconstruction,'reconstruct',seed)
    monkeypatch.setattr(reconstruction,'camera',lambda frame:frame)
    monkeypatch.setattr(reconstruction,'triangulate',triangulate)
    result=prior.training_anchors({'keyframes':frames},tmp_path,[0,2],2)
    assert seen==[frames[0],frames[2]]
    assert [a['frameIndex'] for a in result['anchors']]==[0,2]
    assert all(a['trainingSupportFrames']==[0,2] for a in result['anchors'])
    assert result['failures']==[{'trackId':'failed','reason':'insufficient_observations'}]
    assert result['usedFinalVerifiedPoints'] is False


def test_near_zero_inverse_depth_span_rejects_fake_metric_calibration():
    depth,anchors=fixture()
    for anchor in anchors:anchor['depthMeters']=2.
    with pytest.raises(ValueError,match='near_zero_metric_span'):
        prior.fit_inverse_depth(depth,anchors,frame_index=2,training_frame_indices=[2,4])


def test_cli_preserves_all_selected_failure_entries_without_model(tmp_path):
    document=tmp_path/'document.json'
    document.write_text(json.dumps({'keyframes':[{'imageFile':f'{i}.jpg','pose':{'epoch':2}} for i in range(3)]}))
    output=tmp_path/'output'
    code=prior.main(['--document',str(document),'--directory',str(tmp_path),
        '--output',str(output),'--weights',str(tmp_path/'missing.pth'),'--frame-indices','0,1,2'])
    result=json.loads((output/'result.json').read_text())
    assert code==1 and result['status']=='failed'
    assert [entry['frameIndex'] for entry in result['frames']]==[0,1,2]
    assert all(entry['status']=='failed' for entry in result['frames'])
    assert result['productAccepted'] is False
