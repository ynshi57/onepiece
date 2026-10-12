"""Mathematical renderer/PLY checks, not a scene-quality acceptance test."""
import numpy as np
import pytest

torch=pytest.importorskip('torch')
from app.spatial_splat_cpu import render, export_ply


def parameters():
    return [torch.tensor([[0.,0.,2.]],requires_grad=True),
            torch.tensor([[-2.,-2.,-2.]],requires_grad=True),
            torch.tensor([[1.,0.,0.,0.]],requires_grad=True),
            torch.tensor([[2.,-2.,-2.]],requires_grad=True),
            torch.tensor([2.],requires_grad=True)]


def view():
    return torch.eye(4),torch.tensor([[10.,0.,4.],[0.,10.,4.],[0.,0.,1.]])


def test_anisotropic_covariance_renderer_has_finite_trainable_gradients():
    p=parameters(); image=render(p,view(),8,8)
    assert image.shape==(8,8,3) and torch.isfinite(image).all()
    assert image[4,4,0]>image[4,4,1]
    image.square().mean().backward()
    for value in p:
        assert value.grad is not None and torch.isfinite(value.grad).all()
    assert p[1].grad.abs().sum()>0


def test_behind_camera_is_not_visible():
    p=parameters();p[0]=torch.tensor([[0.,0.,-2.]])
    assert torch.allclose(render(p,view(),8,8),torch.ones(8,8,3))


def test_standard_ply_roundtrip_renders_same_gaussians(tmp_path):
    p=parameters();path=tmp_path/'splats.ply';export_ply(p,path)
    blob=path.read_bytes();offset=blob.index(b'end_header\n')+len(b'end_header\n')
    rows=np.frombuffer(blob[offset:],dtype='<f4').copy().reshape(-1,17)
    assert len(rows)==1 and np.allclose(rows[0,10:13],[-2,-2,-2])
    restored=[torch.tensor(rows[:,:3]),torch.tensor(rows[:,10:13]),torch.tensor(rows[:,13:17]),
              torch.logit((torch.tensor(rows[:,6:9])*.28209479177387814+.5).clamp(.0001,.9999)),torch.tensor(rows[:,9])]
    assert torch.allclose(render(p,view(),8,8),render(restored,view(),8,8),atol=1e-6)


def test_alpha_exposes_uncovered_background_and_full_frame_metrics():
    from app.spatial_splat_cpu import image_metrics
    p=parameters();p[0]=torch.tensor([[0.,0.,-2.]])
    rgb,alpha=render(p,view(),8,8,return_alpha=True)
    metrics=image_metrics(rgb,torch.zeros_like(rgb),alpha)
    assert metrics['mse']==1 and metrics['psnr']==0
    assert metrics['alphaCoverage']==0 and metrics['meanAlpha']==0


def test_training_seed_neighbor_scales_follow_spacing_and_bound_duplicates():
    from app.spatial_splat_cpu import initial_log_scales
    means=torch.tensor([[0.,0.,0.],[.02,0.,0.],[.4,0.,0.],[.4,0.,0.]])
    scales=initial_log_scales(means,'nearest_neighbor').exp()
    assert torch.allclose(scales[0],torch.full((3,),.02))
    assert torch.allclose(scales[2],torch.full((3,),.005))
    assert torch.isfinite(scales).all()


@pytest.mark.parametrize("density",[False,True])
def test_training_holdout_does_not_enter_seed_or_optimization(tmp_path,monkeypatch,density):
    import app.spatial_reconstruction as reconstruction
    from app.spatial_splat_cpu import train
    from PIL import Image
    frames=[]
    for index in range(8):
        name=f'{index}.png'; Image.new('RGB',(32,32),(80,110,140)).save(tmp_path/name)
        frames.append({'imageFile':name,'pose':{'epoch':1}})
    seen=[]
    monkeypatch.setattr(reconstruction,'reconstruct',lambda document,directory: seen.extend(document['keyframes']) or
        {'points':[{'position':{'x':(i%4-1.5)*.1,'y':(i//4-1.5)*.1,'z':2.},'colorRGB':[80,110,140]} for i in range(16)]})
    class Camera:
        width=height=32
        world_to_camera=np.eye(4)
        k=np.array([[20.,0.,16.],[0.,20.,16.],[0.,0.,1.]])
    monkeypatch.setattr(reconstruction,'camera',lambda frame: Camera())
    result=train({'keyframes':frames},tmp_path,tmp_path/'out',epoch=1,iterations=24 if density else 1,maximum_points=16,width=32,
                 density_control=density,density_maximum_points=24)
    assert result['densityControl']['enabled'] is density
    if density:
        assert result['densityControl']['added']>0
        assert 16<result['gaussians']<=24
        assert len(result['densityControl']['events'])==2
    assert frames[3] not in seen
    assert result['evaluationOriginalFrameIndices']==[3]
    assert len(result['initializationEvaluation'])==len(result['evaluation'])==1
    assert result['evaluationImageColumns']==['heldout_truth','initialization','trained']
    assert (tmp_path/'out'/result['evaluation'][0]['alphaImage']).exists()


def test_density_split_is_bounded_preserves_optimizer_and_children_opacity():
    from app.spatial_splat_cpu import split_gaussians
    p=[torch.nn.Parameter(value.detach()) for value in parameters()]
    optimizer=torch.optim.Adam(p,lr=.001)
    render(p,view(),8,8).sum().backward();optimizer.step()
    old_alpha=torch.sigmoid(p[4]).detach().clone()
    original_step=optimizer.state[p[0]]['step'].clone()
    updated,added=split_gaussians(p,torch.tensor([1.]),2,optimizer)
    assert added==1 and len(updated[0])==2
    assert torch.allclose(1-(1-torch.sigmoid(updated[4])).prod(),old_alpha[0])
    assert torch.allclose(optimizer.state[updated[0]]['step'],original_step)
    assert optimizer.state[updated[0]]['exp_avg'].shape==(2,3)
    assert torch.allclose(updated[0][0]+updated[0][1],p[0][0]*2)
    optimizer.zero_grad();render(updated,view(),8,8).sum().backward();optimizer.step()
    same,added=split_gaussians(updated,torch.ones(2),2,optimizer)
    assert added==0 and same is updated


def test_density_never_splits_zero_or_nonfinite_training_gradients():
    from app.spatial_splat_cpu import split_gaussians
    p=[torch.nn.Parameter(value.detach()) for value in parameters()]
    optimizer=torch.optim.Adam(p)
    for score in [0.,float('nan'),float('inf')]:
        same,added=split_gaussians(p,torch.tensor([score]),2,optimizer)
        assert same is p and added==0
