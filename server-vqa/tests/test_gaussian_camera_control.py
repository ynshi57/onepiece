import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy/spatial/research'))
from gaussian_camera_control import partition,transform_sfm,random_ply


def test_partition_retains_all_failures_and_original_split():
    doc={'keyframes':[{'pose':{'epoch':2}} for _ in range(21)]}
    x=partition(doc,[i for i in range(21) if i not in (3,7,10)],2)
    assert x['inputFrames']==21
    assert x['unregisteredIndices']==[3,7,10]
    assert x['unavailableEvaluationIndices']==[3,10]
    assert x['evaluationIndices']==[17]
    assert set(x['trainIndices'])|set(x['evaluationIndices'])==set(x['registeredIndices'])
    with pytest.raises(ValueError):partition(doc,[21],2)


def test_colmap_axes_and_similarity_preserve_projected_rays():
    r=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
    translation=np.array([1.,2.,3.]); world=np.array([2.,4.,7.])
    align=np.array([[1.,0.,0.],[0.,0.,-1.],[0.,1.,0.]])
    shift=np.array([3.,2.,1.]); scale=.3
    c=transform_sfm(r,translation,align,scale,shift)
    local=np.linalg.inv(c)@np.r_[scale*align@world+shift,1]
    assert np.allclose(local[:3],scale*np.diag([1,-1,-1])@(r@world+translation))
    assert np.allclose(c[:3,:3].T@c[:3,:3],np.eye(3))


def test_shared_random_seed_is_deterministic_and_not_photographic():
    p=np.eye(4); data=random_ply([p],42,100)
    assert data==random_ply([p],42,100)
    assert data!=random_ply([p],43,100)
    points=np.frombuffer(data.split(b'end_header\n')[1],dtype='<f4').reshape(100,6)
    assert np.all(points[:,3:]==.5)


def test_missing_evaluation_tail_is_not_silently_dropped(tmp_path):
    from gaussian_camera_control import compare
    with pytest.raises(ValueError,match='incomplete_or_reordered_evaluation_sfm'):
        compare(tmp_path,{'raw':{'evaluation':[{'frameIndex':3},{'frameIndex':10}]},
                          'sfm':{'evaluation':[{'frameIndex':3}]}},2000,[3,10])


def test_intrinsics_require_corner_conversion_and_detect_changes():
    import pycolmap
    from gaussian_camera_control import verify_intrinsics
    frame=dict(intrinsics=np.array([[100,0,32],[0,101,24],[0,0,1]]).ravel(order='F').tolist(),
               cameraToWorld=np.eye(4).ravel(order='F').tolist(),time=0.,width=64,height=48,
               pose=dict(time=0.,epoch=2,position=dict(x=0.,y=0.,z=0.),forward=dict(x=0.,y=0.,z=-1.)))
    good=pycolmap.Camera(model='PINHOLE',width=64,height=48,params=[100,101,32.5,24.5])
    verify_intrinsics(good,frame)
    bad=pycolmap.Camera(model='PINHOLE',width=64,height=48,params=[100,101,32.,24.])
    with pytest.raises(ValueError,match='map_intrinsics_changed'):verify_intrinsics(bad,frame)


def test_source_identity_checks_document_before_training(tmp_path):
    import json
    from gaussian_camera_control import verify_source,sha
    data=tmp_path/'data';data.mkdir();(data/'document.json').write_text('{}')
    upstream=tmp_path/'nonlocal';model=upstream/'map/models/0';model.mkdir(parents=True)
    baseline=tmp_path/'baseline.json';baseline.write_text(json.dumps({'inputDocumentSHA256':'wrong'}))
    (upstream/'result.json').write_text(json.dumps({'baselineSHA256':sha(baseline)}))
    (upstream/'map/inputs.json').write_text('{}')
    with pytest.raises(ValueError,match='source_document_mismatch'):verify_source(data,model,baseline)


def test_camera_receipt_rejects_same_name_changed_pose(tmp_path):
    import json
    from types import SimpleNamespace
    from gaussian_camera_control import verify_map_cameras
    model=tmp_path/'map/models/0';model.mkdir(parents=True)
    receipt={'components':[dict(id=0,registeredNames=['00000.png'],cameras=[dict(frameIndex=0,cameraToWorldCV=np.eye(4).tolist())])]}
    (model.parents[1]/'result.json').write_text(json.dumps(receipt))
    pose=SimpleNamespace(rotation=SimpleNamespace(matrix=lambda:np.eye(3)),translation=np.zeros(3))
    im=SimpleNamespace(name='00000.png',has_pose=True,cam_from_world=lambda:pose)
    rec=SimpleNamespace(images={1:im})
    verify_map_cameras(rec,model)
    pose.translation=np.array([.01,0,0])
    with pytest.raises(ValueError,match='map_camera_pose_receipt_mismatch'):verify_map_cameras(rec,model)


def sparse_fixture(tmp_path, resized=False):
    from types import SimpleNamespace as NS
    from PIL import Image
    frames=[dict(width=8,height=6,imageFile=f'{i}.png') for i in range(3)]
    for i,c in enumerate((20,80,250)):
        Image.new('RGB',(8,6),(c,c,c)).save(tmp_path/f'{i}.png')
    xy=np.array([1.5,2.5])*(.5 if resized else 1.)
    images={i:NS(name=f'{i:05d}.png',camera_id=1,points2D=[NS(xy=xy)]) for i in range(3)}
    elements=lambda ids:[NS(image_id=i,point2D_idx=0) for i in ids]
    points={i:NS(xyz=np.array([i,1.,2.]),color=[255,0,0],track=NS(elements=elements([0,1,2]))) for i in range(16)}
    points[20]=NS(xyz=np.zeros(3),track=NS(elements=elements([0,2])))
    rec=NS(images=images,points3D=points,cameras={1:NS(width=4 if resized else 8,height=3 if resized else 6)})
    return rec,dict(keyframes=frames)


@pytest.mark.parametrize('resized',[False,True])
def test_sparse_seed_training_support_colors_and_coordinate_transform(tmp_path,resized):
    from gaussian_camera_control import sparse_training_seed,WORLD_ROTATION
    rec,doc=sparse_fixture(tmp_path,resized)
    from PIL import Image
    for idx,color in ((0,(120,80,40)),(1,(60,40,20))):
        with Image.open(tmp_path/f'{idx}.png') as im:
            im.putpixel((1,2),color);im.save(tmp_path/f'{idx}.png')
    # Evaluation image may not even be available to the RGB sampler.
    (tmp_path/'2.png').unlink()
    alignment=dict(rotation=np.eye(3).tolist(),scaleToAR=2.,translation=[1,2,3])
    blob,meta=sparse_training_seed(rec,doc,tmp_path,[0,1],alignment)
    values=np.frombuffer(blob.split(b'end_header\n')[1],dtype='<f4').reshape(-1,6)
    assert meta['sourcePointIds']==list(range(16))
    assert meta['rejectedInsufficientTrainingSupport']==[20]
    assert meta['trainingObservationFrames']==[[0,1]]*16
    assert meta['seedColorsUseEvaluationPhotos'] is False
    assert np.allclose(values[:,3:],np.array([90,60,30])/255)
    expected=(2*np.array([rec.points3D[i].xyz for i in range(16)])+[1,2,3])@WORLD_ROTATION[:3,:3].T
    assert np.allclose(values[:,:3],expected)


def test_sparse_seed_insufficient_support_fails_without_random_fallback(tmp_path):
    from gaussian_camera_control import sparse_training_seed
    rec,doc=sparse_fixture(tmp_path)
    with pytest.raises(ValueError,match='sparse_seed_count_outside_control_capacity'):
        sparse_training_seed(rec,doc,tmp_path,[0],{})


def test_sparse_seed_rejects_nonfinite_geometry(tmp_path):
    from gaussian_camera_control import sparse_training_seed
    rec,doc=sparse_fixture(tmp_path);rec.points3D[0].xyz[0]=np.nan
    with pytest.raises(ValueError,match='nonfinite_sparse_seed'):
        sparse_training_seed(rec,doc,tmp_path,[0,1],dict(rotation=np.eye(3),scaleToAR=1.,translation=[0,0,0]))


def test_initialization_parent_rejects_changed_dataset_and_binary(tmp_path,monkeypatch):
    import gaussian_camera_control as g
    from types import SimpleNamespace
    parent=tmp_path/'parent';dataset=parent/'sfm/dataset';dataset.mkdir(parents=True)
    (dataset/'transforms_train.json').write_text('changed')
    (parent/'raw/dataset').mkdir(parents=True)
    (parent/'raw/dataset/transforms_train.json').write_text('original')
    def reproduce(data,model,fresh,baseline,epoch,seed):
        for arm in ('sfm','raw'):
            d=fresh/arm/'dataset';d.mkdir(parents=True)
            (d/'transforms_train.json').write_text('original')
        return {'trainIndices':[0,1]}
    monkeypatch.setattr(g,'prepare',reproduce)
    monkeypatch.setattr(g,'verify_binary_receipt',lambda _: {'binarySHA256':'verified'})
    args=SimpleNamespace(binary=str(tmp_path/'binary'),epoch=2,seed=42,steps=2000,resolution=256)
    source={'binarySHA256':'verified','trainIndices':[0,1],'evaluationIndices':[], 'arms':{'sfm':{'status':'complete_development_only','evaluation':[], 'command':['--total-steps','2000','--max-resolution','256','--seed','42','--max-splats','20000','--sh-degree','0','--growth-select-fraction','.5','--scale-loss-weight','1e-8']}}}
    with pytest.raises(ValueError,match='initialization_parent_dataset_changed'):
        g.verify_initialization_parent(tmp_path,tmp_path,tmp_path,parent,source,args)
    source['binarySHA256']='other'
    with pytest.raises(ValueError,match='initialization_binary_changed'):
        g.verify_initialization_parent(tmp_path,tmp_path,tmp_path,parent,source,args)


def test_preflight_failure_is_persisted_and_existing_output_is_preserved(tmp_path,monkeypatch):
    import json
    import gaussian_camera_control as g
    from types import SimpleNamespace
    args=SimpleNamespace(output=str(tmp_path/'out'))
    def reject(_):raise ValueError('source_changed')
    monkeypatch.setattr(g,'_run_initialization_control',reject)
    with pytest.raises(ValueError,match='source_changed'):g.run_initialization_control(args)
    receipt=json.loads((tmp_path/'out/result.json').read_text())
    assert receipt['status']=='failed_preflight' and receipt['productAccepted'] is False
    before=(tmp_path/'out/result.json').read_bytes()
    with pytest.raises(FileExistsError):g.run_initialization_control(args)
    assert (tmp_path/'out/result.json').read_bytes()==before


def test_completed_arm_binds_metrics_to_rendered_image_and_budget(tmp_path):
    from PIL import Image
    import gaussian_camera_control as g
    dataset=tmp_path/'dataset/images';dataset.mkdir(parents=True)
    artifacts=tmp_path/'artifacts/eval_2000';artifacts.mkdir(parents=True)
    Image.new('RGB',(8,6),(0,0,0)).save(dataset/'00003.jpg')
    Image.new('RGB',(8,6),(255,255,255)).save(artifacts/'00003.png')
    zero=np.zeros((6,8,3));one=np.ones((6,8,3))
    arm={'status':'complete_development_only','command':['--total-steps','2000','--max-resolution','256','--seed','42','--max-splats','20000','--sh-degree','0','--growth-select-fraction','.5','--scale-loss-weight','1e-8'],
         'evaluation':[dict(frameIndex=3,mse=1.,psnr=0.,**g.edge_metrics(zero,one))]}
    assert set(g.verify_completed_arm(tmp_path,arm,2000,256,42,[3]))=={'00003'}
    arm['command'][arm['command'].index('--sh-degree')+1]='3'
    with pytest.raises(ValueError,match='initialization_parent_command_changed_--sh-degree'):
        g.verify_completed_arm(tmp_path,arm,2000,256,42,[3])
    assert set(g.verify_completed_arm(tmp_path,arm,2000,256,42,[3],sh_degree=3))=={'00003'}
    arm['command'][arm['command'].index('--sh-degree')+1]='0'
    Image.new('RGB',(8,6),(0,0,0)).save(artifacts/'00003.png')
    with pytest.raises(ValueError,match='initialization_parent_render_metrics_changed'):
        g.verify_completed_arm(tmp_path,arm,2000,256,42,[3])
    arm['command'][arm['command'].index('--max-splats')+1]='50000'
    with pytest.raises(ValueError,match='initialization_parent_command_changed'):
        g.verify_completed_arm(tmp_path,arm,2000,256,42,[3])
