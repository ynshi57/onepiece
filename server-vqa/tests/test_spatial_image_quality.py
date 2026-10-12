import numpy as np
from PIL import Image
from app.spatial_image_quality import diagnose,matches


def frame(name,t,x=0):
    transform=np.eye(4);transform[0,3]=x
    return {'imageFile':name,'width':160,'height':160,'time':t,'pose':{'epoch':1},'intrinsics':np.eye(3).flatten(order='F').tolist(),'cameraToWorld':transform.flatten(order='F').tolist()}


def test_blank_images_counted_as_not_assessable(tmp_path):
    for name in ['a.jpg','b.jpg']:Image.new('RGB',(160,160),'white').save(tmp_path/name)
    r=diagnose({'keyframes':[frame('a.jpg',0),frame('b.jpg',1,.1)]},tmp_path,feature_method="patch")
    assert r['selectedPairCount']==1 and r['measuredPairCount']==0
    assert r['pairs'][0]['reason']=='insufficient_texture_or_matches'
    assert not r['scaleVerified']


def test_missing_images_and_pair_cap_preserve_denominator(tmp_path):
    r=diagnose({'keyframes':[frame(str(i)+'.jpg',i) for i in range(50)]},tmp_path,feature_method="patch")
    assert r['selectedPairCount']==20
    assert all(p['status']=='failed' for p in r['pairs'])
    assert r['pairs'][0]['sourceKeyframeIndices']==[0,1]
    assert r['pairs'][-1]['sourceKeyframeIndices']==[48,49]


def test_mutual_matching_keeps_unique_descriptors():
    descriptors=np.eye(4)
    result=matches(descriptors,descriptors)
    assert result.tolist()==[[0,0],[1,1],[2,2],[3,3]]


def test_textured_low_baseline_is_not_scale_evidence(tmp_path):
    rng=np.random.default_rng(4)
    image=Image.fromarray(rng.integers(0,255,(160,160),dtype=np.uint8))
    image.save(tmp_path/'a.jpg');image.save(tmp_path/'b.jpg')
    r=diagnose({'keyframes':[frame('a.jpg',0),frame('b.jpg',1)]},tmp_path,feature_method="patch")
    assert r['pairs'][0]['matchCount']>=12
    assert r['pairs'][0]['method']=='rotation_prediction'
    assert r['pairs'][0]['medianRotationPixels']<1
    assert r['measuredPairCount']==1


def test_horizontal_epipolar_matches_have_low_residual(tmp_path):
    rng=np.random.default_rng(4)
    image=rng.integers(0,255,(160,160),dtype=np.uint8)
    Image.fromarray(image).save(tmp_path/'a.jpg')
    shifted=np.zeros_like(image);shifted[:,:-4]=image[:,4:]
    Image.fromarray(shifted).save(tmp_path/'b.jpg')
    r=diagnose({'keyframes':[frame('a.jpg',0),frame('b.jpg',1,.1)]},tmp_path,feature_method="patch")
    assert r['pairs'][0]['status']=='measured'
    assert r['pairs'][0]['medianSampsonPixels']<1
    assert not r['scaleVerified']


def test_native_dimensions_mismatch_visible(tmp_path):
    Image.new('L',(80,80)).save(tmp_path/'a.jpg')
    r=diagnose({'keyframes':[frame('a.jpg',0),frame('b.jpg',1)]},tmp_path,feature_method="patch")
    assert r['selectedPairCount']==1 and r['measuredPairCount']==0
    assert 'native_image_dimensions_mismatch' in r['pairs'][0]['reason']


def test_nonfinite_camera_is_failure_not_valid_measurement(tmp_path):
    rng=np.random.default_rng(4)
    for name in ['a.jpg','b.jpg']:Image.fromarray(rng.integers(0,255,(160,160),dtype=np.uint8)).save(tmp_path/name)
    # Same image ensures enough independent matches to reach camera validation.
    (tmp_path/'b.jpg').write_bytes((tmp_path/'a.jpg').read_bytes())
    a,b=frame('a.jpg',0),frame('b.jpg',1)
    b['intrinsics'][0]=float('nan')
    r=diagnose({'keyframes':[a,b]},tmp_path,feature_method="patch")
    assert r['pairs'][0]['status']=='failed'
    assert 'non_finite_camera_parameters' in r['pairs'][0]['reason']


def test_independent_fundamental_ransac_projection_outliers():
    from app.spatial_image_quality import independent_inliers
    rng=np.random.default_rng(9)
    xyz=rng.uniform(-1,1,(60,3));xyz[:,2]+=4
    a=xyz[:,:2]/xyz[:,2,None]*500+300
    translated=xyz+np.array([.3,.02,0])
    b=translated[:,:2]/translated[:,2,None]*500+300
    b[:15]=rng.uniform(0,600,(15,2))
    mask=independent_inliers(a,b)
    assert mask[15:].sum()>=42
    assert mask[:15].sum()<=3


def test_sift_missing_dependency_exposed_for_every_selected_pair(tmp_path,monkeypatch):
    from app import spatial_sift_native
    def missing():
        raise RuntimeError('native_sift_not_built')
    monkeypatch.setattr(spatial_sift_native, 'library', missing)
    r=diagnose({'keyframes':[frame('a.jpg',0),frame('b.jpg',1)]},tmp_path)
    assert r['featureMethod']=='sift'
    assert r['selectedPairCount']==1 and r['measuredPairCount']==0
    assert r['pairs'][0]['status']=='not_available'


def test_sift_rotation_scale_feature_support(tmp_path):
    from PIL import ImageDraw
    from app.spatial_image_quality import sift_features,sift_matches,cv_support
    rng=np.random.default_rng(3)
    image=Image.new('L',(400,400));draw=ImageDraw.Draw(image)
    for _ in range(150):
        x,y=rng.integers(20,380,2);radius=int(rng.integers(2,9));draw.ellipse((int(x)-radius,int(y)-radius,int(x)+radius,int(y)+radius),fill=int(rng.integers(80,255)))
    angle=np.radians(25);r=.85*np.array([[np.cos(angle),np.sin(angle)],[-np.sin(angle),np.cos(angle)]])
    transform=np.c_[r,np.array([200,200])-r@np.array([200,200])]
    full=np.vstack([transform,[0,0,1]]);inverse=np.linalg.inv(full)
    rotated=image.transform((400,400),Image.Transform.AFFINE,tuple(inverse[:2].ravel()),Image.Resampling.BICUBIC)
    image.save(tmp_path/'a.jpg');rotated.save(tmp_path/'b.jpg')
    a,da=sift_features(tmp_path/'a.jpg');b,db=sift_features(tmp_path/'b.jpg');m=sift_matches(da,db)
    support,_=cv_support(a[m[:,0]],b[m[:,1]])
    assert len(m)>=12 and support.sum()>=12 and support.mean()>=.5
    predicted=np.c_[a[m[:,0]],np.ones(len(m))]@transform.T
    assert np.median(np.linalg.norm(predicted[support]-b[m[support,1]],axis=1))<2


def test_source_sift_blank_frame_and_input_contract():
    from app.spatial_sift_native import extract
    import pytest
    keypoints, descriptors = extract(np.zeros((128,128),dtype=np.uint8))
    assert keypoints.shape == (0,6) and descriptors.shape == (0,128)
    with pytest.raises(ValueError):
        extract(np.zeros((128,128),dtype=np.float32))
    with pytest.raises(ValueError):
        extract(np.zeros((128,128),dtype=np.uint8),maximum=0)
