"""Bounded CPU reference anisotropic Gaussian training (not production Brush).

Independent implementation of projected covariance + depth-sorted alpha blend.
DC colors, optional bounded training-gradient splits, no higher-order SH. Never modifies ARKit geometry.
"""
from pathlib import Path
import json
import math
import time
import os

def write_json(path, value):
    temporary = path.with_name(path.name + ".pending")
    temporary.write_text(json.dumps(value))
    os.replace(temporary, path)

import numpy as np
from PIL import Image


def render(parameters, view, width, height, *, return_alpha=False):
    import torch
    means, log_scales, rotations, color_logits, opacity_logits = parameters
    transform, k = view
    xyz = means @ transform[:3,:3].T + transform[:3,3]
    z = xyz[:,2].clamp(min=.01)
    uv = (xyz @ k.T)[:,:2] / z[:,None]
    quat = rotations / rotations.norm(dim=1, keepdim=True).clamp(min=1e-6)
    w,x,y,qz = quat.unbind(1)
    rot = torch.stack([1-2*(y*y+qz*qz),2*(x*y-w*qz),2*(x*qz+w*y),
                       2*(x*y+w*qz),1-2*(x*x+qz*qz),2*(y*qz-w*x),
                       2*(x*qz-w*y),2*(y*qz+w*x),1-2*(x*x+y*y)],dim=1).reshape(-1,3,3)
    cov = rot @ torch.diag_embed(torch.exp(2*log_scales)) @ rot.transpose(1,2)
    cov_cam = transform[:3,:3] @ cov @ transform[:3,:3].T
    zeros = torch.zeros_like(z)
    j = torch.stack([k[0,0]/z,zeros,-k[0,0]*xyz[:,0]/z**2,
                     zeros,k[1,1]/z,-k[1,1]*xyz[:,1]/z**2],dim=1).reshape(-1,2,3)
    cov2 = j @ cov_cam @ j.transpose(1,2) + torch.eye(2)*.3
    yy,xx = torch.meshgrid(torch.arange(height),torch.arange(width),indexing='ij')
    pixels = torch.stack([xx,yy],dim=-1).float().reshape(-1,2)
    delta = pixels[None] - uv[:,None]
    power = torch.einsum('npi,nij,npj->np',delta,torch.linalg.inv(cov2),delta)
    visible = (xyz[:,2] > .05).float()
    alpha = (torch.sigmoid(opacity_logits)[:,None]*torch.exp(-.5*power)*visible[:,None]).clamp(max=.99)
    order = torch.argsort(xyz[:,2].detach())
    alpha = alpha[order]
    transmission = torch.cumprod(torch.cat([torch.ones(1,width*height),1-alpha+1e-8],dim=0),dim=0)
    weights = alpha * transmission[:-1]
    rgb = weights.T @ torch.sigmoid(color_logits)[order] + transmission[-1,:,None]
    rgb = rgb.reshape(height,width,3)
    return (rgb, (1-transmission[-1]).reshape(height,width)) if return_alpha else rgb


def initial_log_scales(means, method):
    """Training-seed-only nearest-neighbor footprint; duplicates cannot create zero scales."""
    import torch
    if method == 'fixed_legacy':
        return torch.full_like(means, math.log(.08))
    distances = torch.cdist(means, means)
    distances.fill_diagonal_(float('inf'))
    nearest = distances.min(dim=1).values.clamp(.005, 1.)
    return nearest.log()[:,None].expand(-1,3).clone()


def image_metrics(predicted, target, alpha):
    """Full-frame metrics, including uncovered white background (never foreground masked)."""
    mse=float(((predicted.clamp(0,1)-target)**2).mean())
    return {'psnr':-10*math.log10(max(mse,1e-12)), 'mse':mse,
            'meanAlpha':float(alpha.mean()),
            'alphaCoverage':float((alpha>=.5).float().mean())}


def split_gaussians(parameters, scores, capacity, optimizer):
    """Split high training-gradient parents; divide opacity budget and retain Adam history."""
    import torch
    count = len(parameters[0])
    added = min(max(0, capacity-count), max(1,count//5))
    eligible = torch.where(torch.isfinite(scores) & (scores>0))[0]
    added = min(added,len(eligible))
    if not added:
        return parameters, 0
    chosen = eligible[torch.argsort(scores[eligible],descending=True)[:added]]
    source = torch.cat([torch.arange(count),chosen])
    values = [p.detach()[source].clone() for p in parameters]
    scales = parameters[1].detach()[chosen].exp()
    axis = scales.argmax(dim=1)
    local = torch.nn.functional.one_hot(axis,3).float() * scales.gather(1,axis[:,None]) * .5
    quaternion=parameters[2].detach()[chosen]
    quaternion=quaternion/quaternion.norm(dim=1,keepdim=True).clamp(min=1e-6)
    vector=quaternion[:,1:]; scalar=quaternion[:,:1]
    cross=torch.cross(vector,local,dim=1)
    offset=local+2*(scalar*cross+torch.cross(vector,cross,dim=1))
    values[0][chosen]-=offset; values[0][count:]+=offset
    values[1][chosen]-=math.log(1.6); values[1][count:]-=math.log(1.6)
    values[1].clamp_(math.log(.005),math.log(1.))
    child_opacity = 1-torch.sqrt(1-torch.sigmoid(parameters[4].detach()[chosen]))
    child_logits = torch.logit(child_opacity.clamp(1e-5,1-1e-5))
    values[4][chosen]=child_logits; values[4][count:]=child_logits
    replacements = [torch.nn.Parameter(v) for v in values]
    for old,new in zip(parameters,replacements):
        for group in optimizer.param_groups:
            group['params']=[new if value is old else value for value in group['params']]
        state = optimizer.state.pop(old,{})
        optimizer.state[new] = {key: value[source].clone() if torch.is_tensor(value) and value.shape==old.shape
                                else value.clone() if torch.is_tensor(value) else value for key,value in state.items()}
    return replacements, added


def train(document, directory: Path, output: Path, *, epoch: int, iterations=240,
          maximum_points=600, width=64, initialization="nearest_neighbor",
          density_control=False, density_maximum_points=1000):
    import torch
    from .spatial_reconstruction import reconstruct, camera, resize_intrinsics
    if output.exists():
        raise ValueError('splat_output_already_exists')
    if not 1 <= iterations <= 1000 or not 32 <= width <= 128 or not 16 <= maximum_points <= 1000:
        raise ValueError('invalid_reference_training_capacity')
    if initialization not in {'fixed_legacy', 'nearest_neighbor'}:
        raise ValueError('invalid_splat_initialization')
    if not isinstance(density_control,bool) or not maximum_points <= density_maximum_points <= 1000:
        raise ValueError('invalid_density_control_capacity')
    frames = [f for f in document['keyframes'] if f['pose']['epoch']==epoch]
    if len(frames)<8:
        raise ValueError('at_least_eight_frames_required')
    train_frames = [f for i,f in enumerate(frames) if i%7!=3]
    eval_frames = [f for i,f in enumerate(frames) if i%7==3]
    # Evaluation RGB never enters SIFT seed, initialization, loss or stopping.
    start = time.monotonic()
    training_doc = {**document,'keyframes':train_frames}
    seed = reconstruct(training_doc,directory)
    seed_seconds = time.monotonic()-start
    points = seed.get('points',[])
    if len(points)<16:
        raise ValueError('insufficient_training_only_sparse_seed')
    indices = np.linspace(0,len(points)-1,min(maximum_points,len(points)),dtype=int)
    selected = [points[i] for i in indices]
    torch.set_num_threads(2)
    torch.manual_seed(0)
    means = torch.nn.Parameter(torch.tensor([[p['position'][a] for a in 'xyz'] for p in selected],dtype=torch.float32))
    colors = torch.tensor([p.get('colorRGB',[128,128,128]) for p in selected],dtype=torch.float32)/255
    color = torch.nn.Parameter(torch.logit(colors.clamp(.01,.99)))
    scales = torch.nn.Parameter(initial_log_scales(means.detach(), initialization))
    rotations = torch.nn.Parameter(torch.tensor([[1.,0.,0.,0.]]*len(selected)))
    opacity = torch.nn.Parameter(torch.full((len(selected),),-1.))
    parameters = [means,scales,rotations,color,opacity]
    optimizer = torch.optim.Adam([{'params':[means],'lr':.001}, {'params':[scales,rotations],'lr':.01},
                                  {'params':[color,opacity],'lr':.03}])
    def prepare(frame):
        cam = camera(frame)
        height = max(1,round(cam.height*width/cam.width))
        with Image.open(directory/frame['imageFile']) as image:
            if image.size!=(cam.width,cam.height): raise ValueError('image_camera_size_mismatch')
            rgb = torch.tensor(np.array(image.convert('RGB').resize((width,height),Image.Resampling.LANCZOS)).copy(),dtype=torch.float32)/255
        k = resize_intrinsics(cam.k,cam.width,cam.height,width,height)
        return (torch.tensor(cam.world_to_camera,dtype=torch.float32),torch.tensor(k,dtype=torch.float32)),rgb,height
    views = [prepare(f) for f in train_frames]
    # Holdout images are opened only after optimization; baseline is an independent snapshot.
    initial_parameters = [p.detach().clone() for p in parameters]
    history=[]
    density_events=[]
    gradient_scores=torch.zeros(len(means))
    density_steps={iterations//3,2*iterations//3}
    output.mkdir(parents=True)
    try:
        optimization_start = time.monotonic()
        for step in range(iterations):
            view,target,height=views[step%len(views)]
            optimizer.zero_grad()
            predicted=render(parameters,view,width,height)
            loss=(predicted-target).abs().mean()
            if not torch.isfinite(loss): raise RuntimeError('nonfinite_training_loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(parameters,10.)
            if density_control:
                gradient_scores += (means.grad.detach()*scales.detach().exp()).norm(dim=1)
            optimizer.step()
            with torch.no_grad(): scales.clamp_(math.log(.005),math.log(1.))
            history.append(float(loss.detach()))
            if density_control and step+1 in density_steps and step+1>=len(views):
                density_start=time.monotonic()
                before=len(means)
                parameters, added=split_gaussians(parameters,gradient_scores,density_maximum_points,optimizer)
                means,scales,rotations,color,opacity=parameters
                gradient_scores=torch.zeros(len(means))
                density_events.append({'iteration':step+1,'before':before,'added':added,'after':len(means),
                                       'elapsedSeconds':time.monotonic()-density_start,
                                       'criterion':'accumulated_training_mean_gradient_times_scale'})
            if step%20==0:
                write_json(output/'progress.json', {'status':'training','iteration':step,'iterations':iterations,'loss':history[-1]})
        optimization_seconds = time.monotonic()-optimization_start
        evaluation_start = time.monotonic()
        evaluations = [prepare(f) for f in eval_frames]
        metrics=[]
        baseline_metrics=[]
        with torch.no_grad():
            for index,(view,target,height) in enumerate(evaluations):
                baseline, baseline_alpha=render(initial_parameters,view,width,height,return_alpha=True)
                predicted, alpha=render(parameters,view,width,height,return_alpha=True)
                baseline_metrics.append(image_metrics(baseline,target,baseline_alpha))
                diagnostic=image_metrics(predicted,target,alpha)
                image=np.uint8(predicted.clamp(0,1).numpy()*255)
                initial_image=np.uint8(baseline.clamp(0,1).numpy()*255)
                truth=np.uint8(target.numpy()*255)
                Image.fromarray(np.concatenate([truth,initial_image,image],axis=1)).save(output/f'evaluation-{index:03d}.png')
                Image.fromarray(np.uint8(alpha.numpy()*255)).save(output/f'evaluation-alpha-{index:03d}.png')
                metrics.append({'evaluationIndex':index,'sourceImageFile':eval_frames[index]['imageFile'],'image':f'evaluation-{index:03d}.png',
                                'alphaImage':f'evaluation-alpha-{index:03d}.png',**diagnostic})
        evaluation_seconds = time.monotonic()-evaluation_start
        export_ply(parameters,output/'splats.ply')
        model={'means':means.detach().tolist(),'logScales':scales.detach().tolist(),
               'rotations':rotations.detach().tolist(),'colors':torch.sigmoid(color).detach().tolist(),
               'opacities':torch.sigmoid(opacity).detach().tolist()}
        (output/'splats.json').write_text(json.dumps(model))
        result={'status':'trained_reference_experiment','productAccepted':False,'epoch':epoch,
                'iterations':iterations,'gaussians':len(means),'initialGaussians':len(selected),
                'densityControl':{'enabled':density_control,'capacity':density_maximum_points,'events':density_events,
                                  'added':len(means)-len(selected),'evaluationUsed':False},'seedPoints':len(points),
                'trainFrames':len(train_frames),'evaluationFrames':len(eval_frames),
                'trainOriginalFrameIndices':[i for i,f in enumerate(document['keyframes']) if f in train_frames],
                'evaluationOriginalFrameIndices':[i for i,f in enumerate(document['keyframes']) if f in eval_frames],
                'evaluationRGBExcludedFromSeedAndOptimization':True,'renderWidth':width,
                'elapsedSeconds':time.monotonic()-start,
                'phaseSeconds':{'seed':seed_seconds,'optimization':optimization_seconds,'evaluation':evaluation_seconds},
                'trainingLoss':history,
                'initialization':initialization,'initializationEvaluation':baseline_metrics,
                'evaluationImageColumns':['heldout_truth','initialization','trained'],
                'alphaCoverageThreshold':.5,'evaluation':metrics,'modelFile':'splats.ply',
                'limitations':['CPU_low_resolution_reference','DC_color_only',*(['experimental_gradient_density_control'] if density_control else ['no_density_control']),
                               'raw_ARKit_poses_unverified','not_complete_room_geometry',
                               'not_production_quality_or_real_time']}
        write_json(output/'result.json', result)
        write_json(output/'progress.json', {'status':result['status']})
        return result
    except Exception as exc:
        write_json(output/'progress.json', {'status':'failed','reason':str(exc)})
        raise


def export_ply(parameters,path):
    import torch
    means,scales,rotation,colors,opacity=parameters
    # Standard 3DGS fields: SH DC, preactivation opacity/log scales, unit wxyz.
    dc=(torch.sigmoid(colors)-.5)/.28209479177387814
    quat=rotation/rotation.norm(dim=1,keepdim=True).clamp(min=1e-6)
    rows=torch.cat([means,torch.zeros_like(means),dc,opacity[:,None],scales,quat],dim=1).detach().numpy()
    names=['x','y','z','nx','ny','nz','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
    header='ply\nformat binary_little_endian 1.0\nelement vertex '+str(len(rows))+'\n'+''.join('property float '+n+'\n' for n in names)+'end_header\n'
    with path.open('wb') as f: f.write(header.encode()); f.write(rows.astype('<f4').tobytes())
