"""Known-camera appearance development control; never independent geometry acceptance.

All images participated in SfM. Both arms retain the same registered intersection,
RGB holdout and identical random PLY. Unregistered frames remain in the denominator.
"""
import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
import sys
import time
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server-vqa'))
from app.spatial_brush import (edge_metrics, expected_render_size, validate_model,
                               verify_binary_receipt)
from app.spatial_reconstruction import camera, resize_intrinsics
from app.spatial_splat_dataset import WORLD_ROTATION
from learned_map_query import align_sensor


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False))


def partition(document, registered, epoch):
    all_indices = [i for i, f in enumerate(document['keyframes']) if f['pose']['epoch'] == epoch]
    registered = set(registered)
    if not registered.issubset(all_indices):
        raise ValueError('map_outside_selected_epoch')
    hold = {i for sequence, i in enumerate(all_indices) if sequence % 7 == 3}
    common = sorted(registered)
    return dict(inputFrames=len(all_indices), inputIndices=all_indices, registeredIndices=common,
                unregisteredIndices=sorted(set(all_indices)-registered),
                trainIndices=[i for i in common if i not in hold],
                evaluationIndices=[i for i in common if i in hold],
                unavailableEvaluationIndices=sorted(hold-registered))


def transform_sfm(rotation_cv, translation_cv, alignment_rotation, scale, translation):
    """COLMAP world-to-CV -> OpenGL camera-to-AR-prior world."""
    c = np.eye(4)
    c[:3, :3] = alignment_rotation @ rotation_cv.T @ np.diag([1., -1., -1.])
    c[:3, 3] = scale * alignment_rotation @ (-rotation_cv.T @ translation_cv) + translation
    return c


def random_ply(poses, seed=42, count=2000):
    # Same union-of-training-camera domain in both arms; no photo colors or SfM points.
    anchors = np.array([p[:3, 3] - depth * p[:3, 2] for p in poses for depth in (0., 3.)])
    low, high = anchors.min(0)-.5, anchors.max(0)+.5
    xyz = np.random.default_rng(seed).uniform(low, high, (count, 3)) @ WORLD_ROTATION[:3, :3].T
    header = 'ply\nformat binary_little_endian 1.0\nelement vertex '+str(count)+'\n'
    header += ''.join('property float '+n+'\n' for n in ('x','y','z','red','green','blue'))+'end_header\n'
    return header.encode()+np.column_stack((xyz, np.full((count, 3), .5))).astype('<f4').tobytes()


def verify_source(data, model, baseline_path):
    baseline = json.loads(baseline_path.read_text())
    upstream = model.parents[2]
    receipt_path = upstream/'result.json'
    receipt = json.loads(receipt_path.read_text())
    inputs_path = model.parents[1]/'inputs.json'
    inputs = json.loads(inputs_path.read_text())
    if receipt['baselineSHA256'] != sha(baseline_path): raise ValueError('baseline_receipt_mismatch')
    if baseline['inputDocumentSHA256'] != sha(data/'document.json'): raise ValueError('source_document_mismatch')
    if inputs['trainingIndices'] != baseline['trainingIndices'] or inputs['evaluationExcluded']:
        raise ValueError('map_frame_identity_mismatch')
    if inputs['pixelConvention'] != 'native_center_archives_COLMAP_corner_points_and_K_plus_half':
        raise ValueError('unknown_map_pixel_convention')
    if receipt['map'] != json.loads((model.parents[1]/'result.json').read_text()):
        raise ValueError('map_receipt_mismatch')
    doc=json.loads((data/'document.json').read_text())
    rows=baseline['features']
    if sorted(r['frameIndex'] for r in rows) != sorted(inputs['trainingIndices']):
        raise ValueError('photo_receipts_incomplete')
    for row in rows:
        i=row['frameIndex']; path=(data/doc['keyframes'][i]['imageFile']).resolve()
        if path.parent != data or sha(path) != row['imageSHA256']: raise ValueError('photo_identity_mismatch')
    return dict(baselineSHA256=sha(baseline_path), nonlocalReceiptSHA256=sha(receipt_path), mapInputsSHA256=sha(inputs_path))


def verify_map_cameras(rec, model):
    receipt=json.loads((model.parents[1]/'result.json').read_text())
    matches=[c for c in receipt['components'] if str(c['id']) == model.name]
    if len(matches)!=1: raise ValueError('map_component_identity_mismatch')
    component=matches[0]
    actual={im.name:im for im in rec.images.values() if im.has_pose}
    if set(actual)!=set(component['registeredNames']): raise ValueError('map_camera_names_mismatch')
    expected={c['frameIndex']:np.asarray(c['cameraToWorldCV']) for c in component['cameras']}
    if sorted(expected)!=sorted(int(Path(n).stem) for n in actual): raise ValueError('map_camera_indices_mismatch')
    for name, im in actual.items():
        pose=im.cam_from_world(); r=pose.rotation.matrix(); c=np.eye(4)
        c[:3,:3]=r.T; c[:3,3]=-r.T@pose.translation
        if not np.allclose(c,expected[int(Path(name).stem)],atol=1e-9,rtol=0):
            raise ValueError('map_camera_pose_receipt_mismatch')


def verify_intrinsics(colmap_camera, frame):
    original=camera(frame)
    if colmap_camera.model.name != 'PINHOLE': raise ValueError('unsupported_map_camera')
    k=np.array(colmap_camera.calibration_matrix(),dtype=float)
    k[0,2]-=.5; k[1,2]-=.5
    expected=resize_intrinsics(original.k, original.width, original.height, colmap_camera.width, colmap_camera.height)
    if not np.allclose(k,expected,atol=1e-5,rtol=0): raise ValueError('map_intrinsics_changed')


def prepare(data, model, output, baseline_path, epoch=2, seed=42):
    import pycolmap
    source_receipt=verify_source(data,model,baseline_path)
    doc = json.loads((data/'document.json').read_text())
    rec = pycolmap.Reconstruction(str(model))
    verify_map_cameras(rec,model)
    registered = sorted((int(Path(im.name).stem), im) for im in rec.images.values() if im.has_pose)
    split = partition(doc, [i for i, _ in registered], epoch)
    if len(split['trainIndices']) < 8 or not split['evaluationIndices']:
        raise ValueError('insufficient_common_train_or_evaluation')
    raw = {i: camera(doc['keyframes'][i]).camera_to_world for i, _ in registered}
    centers, rotations = [], []
    for i, im in registered:
        verify_intrinsics(rec.cameras[im.camera_id],doc['keyframes'][i])
        pose = im.cam_from_world(); r = pose.rotation.matrix()
        centers.append(-r.T @ pose.translation)
        rotations.append(r.T @ np.diag([1., -1., -1.]))
    R, scale, t, keep, angles, errors = align_sensor(centers, rotations, [raw[i] for i, _ in registered])
    sfm = {i: transform_sfm(im.cam_from_world().rotation.matrix(), im.cam_from_world().translation, R, scale, t)
           for i, im in registered}
    shared = random_ply([poses[i] for poses in (raw, sfm) for i in split['trainIndices']], seed)
    photo_receipts = []
    for i in split['inputIndices']:
        p = (data/doc['keyframes'][i]['imageFile']).resolve()
        if p.parent != data or not p.is_file(): raise ValueError('invalid_photo')
        photo_receipts.append(dict(frameIndex=i, sha256=sha(p)))
    for arm, poses in (('raw', raw), ('sfm', sfm)):
        dataset = output/arm/'dataset'; (dataset/'images').mkdir(parents=True)
        (dataset/'shared-random.ply').write_bytes(shared)
        groups = {'train': [], 'val': []}
        for i in split['registeredIndices']:
            f = doc['keyframes'][i]; cam = camera(f)
            with Image.open(data/f['imageFile']) as image:
                if image.size != (cam.width, cam.height): raise ValueError('photo_size_mismatch')
                w = min(640, image.width); h = round(image.height*w/image.width)
                image.convert('RGB').resize((w,h), Image.Resampling.LANCZOS).save(dataset/'images'/f'{i:05d}.jpg', quality=95)
            k = resize_intrinsics(cam.k, cam.width, cam.height, w, h)
            row = dict(file_path=f'images/{i:05d}.jpg', transform_matrix=(WORLD_ROTATION @ poses[i]).tolist(),
                       fl_x=float(k[0,0]), fl_y=float(k[1,1]), cx=float(k[0,2]), cy=float(k[1,2]), w=w, h=h)
            groups['train' if i in split['trainIndices'] else 'val'].append(row)
        for name, frames in groups.items():
            scene = dict(camera_model='OPENCV', frames=frames)
            if name == 'train': scene['ply_file_path'] = 'shared-random.ply'
            write(dataset/f'transforms_{name}.json', scene)
    return dict(**split, sourceVerification=source_receipt, protocol='all_image_SfM_known_camera_appearance_development_only',
                productAccepted=False, independentGeometryEvaluation=False,
                evaluationRGBExcludedFromAppearanceLoss=True, evaluationImagesUsedInSfM=True,
                initialization='identical_random_PLY_no_RGB_colors_no_SfM_points',
                seedSHA256=hashlib.sha256(shared).hexdigest(), photoReceipts=photo_receipts,
                inputDocumentSHA256=sha(data/'document.json'),
                mapSHA256={p.name:sha(p) for p in model.iterdir() if p.is_file()},
                alignment=dict(rotation=R.tolist(), scaleToAR=float(scale), translation=t.tolist(),
                               metricTruth=False, rejectedForSensorAgreementButNotDropped=[i for (i,_),k in zip(registered,keep) if not k],
                               centerResiduals=errors.tolist(), angleResiduals=angles.tolist()),
                limitations=['same scan reused for research; not untouched test set',
                             'arm-specific evaluation poses; tests complete camera solution, not training poses alone',
                             'intrinsics fixed to original per-frame camera in both arms',
                             'same GPU seed does not guarantee bitwise deterministic optimization',
                             'unregistered frames remain coverage failures; appearance mean conditional on common registered views'])


def train_arm(output, arm, binary, steps, resolution, seed, max_splats=20000, sh_degree=0):
    root=output/arm; artifacts=root/'artifacts'; artifacts.mkdir()
    command=[str(binary),str(root/'dataset'),'--total-steps',str(steps),'--max-resolution',str(resolution),
             '--max-splats',str(max_splats),'--sh-degree',str(sh_degree),'--growth-select-fraction','.5',
             '--scale-loss-weight','1e-8','--seed',str(seed),'--eval-every',str(steps),
             '--eval-save-to-disk','--export-every',str(steps),'--export-path',str(artifacts)]
    start=time.monotonic(); result=dict(status='training',command=command)
    write(root/'result.json', result)
    try:
        with (binary.parent.parent/'brush-training.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with (root/'trainer.log').open('w') as log:
                proc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,cwd=root,timeout=7200)
            if proc.returncode: raise RuntimeError('trainer_exit_'+str(proc.returncode))
        result['gaussians']=validate_model(artifacts/f'export_{steps}.ply',maximum_splats=max_splats,sh_degree=sh_degree)
        measured=[]
        for frame in json.loads((root/'dataset/transforms_val.json').read_text())['frames']:
            stem=Path(frame['file_path']).stem
            pred_path=artifacts/f'eval_{steps}'/f'{stem}.png'
            with Image.open(pred_path) as im: prediction=im.convert('RGB'); size=im.size
            with Image.open(root/'dataset'/frame['file_path']) as im:
                if size != expected_render_size(im.size,resolution): raise ValueError('render_size_mismatch')
                truth=im.convert('RGB').resize(size,Image.Resampling.LANCZOS)
            a=np.asarray(truth,dtype=float)/255; b=np.asarray(prediction,dtype=float)/255
            mse=float(np.mean((a-b)**2))
            measured.append(dict(frameIndex=int(stem),mse=mse,psnr=float(-10*np.log10(max(mse,1e-12))),**edge_metrics(a,b)))
        result.update(status='complete_development_only',evaluation=measured,productAccepted=False)
    except Exception as exc:
        result.update(status='failed',error=str(exc)); raise
    finally:
        result['elapsedSeconds']=time.monotonic()-start; write(root/'result.json',result)
    return result


def compare(output, arms, steps, expected_indices):
    for arm in ('raw','sfm'):
        indices=[r['frameIndex'] for r in arms[arm]['evaluation']]
        if indices != expected_indices: raise ValueError('incomplete_or_reordered_evaluation_'+arm)
    rows=[]
    for raw, sfm in zip(arms['raw']['evaluation'],arms['sfm']['evaluation']):
        i=raw['frameIndex']; assert i == sfm['frameIndex']
        stem=f'{i:05d}'; source=output/'raw/dataset/images'/f'{stem}.jpg'
        with Image.open(output/'raw/artifacts'/f'eval_{steps}'/f'{stem}.png') as p: size=p.size; a=p.convert('RGB')
        with Image.open(output/'sfm/artifacts'/f'eval_{steps}'/f'{stem}.png') as p: b=p.convert('RGB')
        with Image.open(source) as p: truth=p.convert('RGB').resize(size,Image.Resampling.LANCZOS)
        panel=Image.new('RGB',(size[0]*3,size[1])); panel.paste(truth,(0,0)); panel.paste(a,(size[0],0)); panel.paste(b,(size[0]*2,0))
        panel.save(output/f'compare-{stem}.png')
        rows.append(dict(frameIndex=i,rawPSNR=raw['psnr'],sfmPSNR=sfm['psnr'],deltaPSNR=sfm['psnr']-raw['psnr'],rawEdgeF1=raw['edgeF1'],sfmEdgeF1=sfm['edgeF1'],image=f'compare-{stem}.png'))
    return rows


def sparse_training_seed(rec, document, data, training, alignment):
    """Geometry is all-image SfM; RGB comes only from >=2 training observations."""
    training=set(training); selected=[]; requests={}; rejected=[]
    for pid, point in sorted(rec.points3D.items()):
        observations=[]
        for e in point.track.elements:
            im=rec.images[e.image_id]; idx=int(Path(im.name).stem)
            if idx not in training: continue
            f=document['keyframes'][idx]
            cam=rec.cameras[im.camera_id]
            xy=(np.asarray(im.points2D[e.point2D_idx].xy) /
                np.array([cam.width/f['width'],cam.height/f['height']]))-.5
            if not np.isfinite(xy).all() or not (0<=xy[0]<f['width'] and 0<=xy[1]<f['height']): continue
            observations.append((idx,xy))
        if len({idx for idx,_ in observations})<2:
            rejected.append(int(pid)); continue
        slot=len(selected); selected.append((pid,point,observations))
        for idx,xy in observations: requests.setdefault(idx,[]).append((slot,xy))
    if not 16<=len(selected)<=20000: raise ValueError('sparse_seed_count_outside_control_capacity')
    colors=[[] for _ in selected]
    for idx, samples in requests.items():
        with Image.open(data/document['keyframes'][idx]['imageFile']) as im:
            f=document['keyframes'][idx]
            if im.size != (f['width'],f['height']): raise ValueError('seed_photo_size_mismatch')
            rgb=np.asarray(im.convert('RGB'))
        for slot,xy in samples:
            x,y=np.floor(xy+.5).astype(int);x=min(x,rgb.shape[1]-1);y=min(y,rgb.shape[0]-1)
            colors[slot].append(rgb[y,x]/255.)
    R=np.asarray(alignment['rotation']);scale=alignment['scaleToAR'];t=np.asarray(alignment['translation'])
    xyz=np.asarray([scale*R@point.xyz+t for _,point,_ in selected])@WORLD_ROTATION[:3,:3].T
    color=np.asarray([np.median(c,axis=0) for c in colors])
    if not np.isfinite(xyz).all() or not np.isfinite(color).all():raise ValueError('nonfinite_sparse_seed')
    header='ply\nformat binary_little_endian 1.0\nelement vertex '+str(len(selected))+'\n'
    header+=''.join('property float '+n+'\n' for n in ('x','y','z','red','green','blue'))+'end_header\n'
    from scipy.spatial import cKDTree
    neighbor=cKDTree(xyz).query(xyz,k=2)[0][:,1]
    metadata=dict(seedPoints=len(selected),inputMapPoints=len(rec.points3D),rejectedInsufficientTrainingSupport=rejected,
                  geometryUsesAllImageSfM=True,seedColorsUseEvaluationPhotos=False,
                  colorSampling='median training observations native nearest pixel; COLMAP corner / image scale minus .5',
                  sourcePointIds=[int(pid) for pid,_,_ in selected],
                  trainingObservationFrames=[sorted({idx for idx,_ in obs}) for _,_,obs in selected],
                  nearestNeighborDistanceMeters=dict(min=float(neighbor.min()),median=float(np.median(neighbor)),p95=float(np.quantile(neighbor,.95)),max=float(neighbor.max()),zero=int((neighbor==0).sum())),
                  scaleIsSensorAgreementNotTruth=True)
    return header.encode()+np.column_stack((xyz,color)).astype('<f4').tobytes(),metadata


def verify_completed_arm(root, arm_result, steps, resolution, seed, expected_indices, max_splats=20000, sh_degree=0):
    if arm_result['status']!='complete_development_only':
        raise ValueError('initialization_parent_arm_incomplete')
    command=arm_result['command']
    for flag,value in (('--total-steps',steps),('--max-resolution',resolution),
                       ('--seed',seed),('--max-splats',max_splats),('--sh-degree',sh_degree),
                       ('--growth-select-fraction',.5),('--scale-loss-weight',1e-8)):
        if command.count(flag)!=1 or float(command[command.index(flag)+1])!=float(value):
            raise ValueError('initialization_parent_command_changed_'+flag)
    rows=arm_result['evaluation']
    if [r['frameIndex'] for r in rows]!=expected_indices:
        raise ValueError('initialization_parent_evaluation_incomplete')
    hashes={}
    for row in rows:
        stem=f"{row['frameIndex']:05d}"
        hashes[stem]=sha(root/'artifacts'/f'eval_{steps}'/f'{stem}.png')
        with Image.open(root/'artifacts'/f'eval_{steps}'/f'{stem}.png') as im:
            prediction=np.asarray(im.convert('RGB'),dtype=float)/255;size=im.size
        with Image.open(root/'dataset/images'/f'{stem}.jpg') as im:
            if size!=expected_render_size(im.size,resolution):raise ValueError('parent_render_size_mismatch')
            truth=np.asarray(im.convert('RGB').resize(size,Image.Resampling.LANCZOS),dtype=float)/255
        mse=float(np.mean((truth-prediction)**2))
        measured=dict(mse=mse,psnr=float(-10*np.log10(max(mse,1e-12))),**edge_metrics(truth,prediction))
        if any((row[k]!=v if v is None or isinstance(v,str) else not np.isclose(row[k],v,atol=1e-10,rtol=0)) for k,v in measured.items()):
            raise ValueError('initialization_parent_render_metrics_changed')
    return hashes


def verify_initialization_parent(data, model, baseline, parent, source, args):
    receipt=verify_binary_receipt(Path(args.binary).resolve())
    if receipt['binarySHA256'] != source['binarySHA256']:
        raise ValueError('initialization_binary_changed')
    # Legacy parent receipts omit budget summaries; the actual command below is mandatory.
    fixed=dict(maxSplats=20000,shDegree=0,growthFraction=.5,scaleLossWeight=1e-8,randomSeedPoints=2000)
    if any(k in source and source[k]!=v for k,v in fixed.items()):
        raise ValueError('initialization_parent_budget_changed')
    render_hashes=verify_completed_arm(parent/'sfm',source['arms']['sfm'],args.steps,args.resolution,args.seed,source['evaluationIndices'])
    with tempfile.TemporaryDirectory(prefix='vqasee-init-verify-') as temporary:
        fresh=Path(temporary)
        expected=prepare(data,model,fresh,baseline,args.epoch,args.seed)
        for key in expected:
            if key not in ('limitations',) and expected[key] != source[key]:
                raise ValueError('initialization_parent_metadata_changed_'+key)
        for arm in ('sfm','raw'):
            original=parent/arm/'dataset'; reproduced=fresh/arm/'dataset'
            files={p.relative_to(reproduced) for p in reproduced.rglob('*') if p.is_file()}
            if files!={p.relative_to(original) for p in original.rglob('*') if p.is_file()}:
                raise ValueError('initialization_parent_dataset_files_changed')
            if any(sha(original/p)!=sha(reproduced/p) for p in files):
                raise ValueError('initialization_parent_dataset_changed')
    return render_hashes


def _run_initialization_control(args):
    import pycolmap
    data,model,out,binary,parent=map(lambda x:Path(x).resolve(),(args.data,args.model,args.output,args.binary,args.initialization_control_parent))
    verify_binary_receipt(binary);verify_source(data,model,Path(args.baseline).resolve())
    rec=pycolmap.Reconstruction(str(model));verify_map_cameras(rec,model)
    source=json.loads((parent/'result.json').read_text())
    if source['status']!='complete_development_only' or source['inputDocumentSHA256']!=sha(data/'document.json'):
        raise ValueError('invalid_initialization_baseline')
    if (source['inputFrames'],len(source['trainIndices']),len(source['evaluationIndices']))!=(226,175,27):
        raise ValueError('initialization_control_scan_partition_changed')
    if source['mapSHA256']!={p.name:sha(p) for p in model.iterdir() if p.is_file()}:raise ValueError('initialization_map_changed')
    if (args.steps,args.resolution,args.seed)!=(source['steps'],source['resolution'],source['seed']):raise ValueError('initialization_configuration_changed')
    verified_parent=verify_initialization_parent(data,model,Path(args.baseline).resolve(),parent,source,args)
    out.mkdir(parents=True,exist_ok=False)
    result={k:v for k,v in source.items() if k not in ('arms','comparison','comparisonColumns','status')}
    result.update(status='preparing',control='SfM_random_vs_SfM_training_supported_sparse_initialization',
                  parentResultSHA256=sha(parent/'result.json'),verifiedParentRenderSHA256=verified_parent,productAccepted=False,
                  limitations=source['limitations']+['initialization location, colors, point count and neighbor scales change together; not geometry-only ablation'])
    write(out/'result.json',result)
    try:
        doc=json.loads((data/'document.json').read_text())
        seed,metadata=sparse_training_seed(rec,doc,data,source['trainIndices'],source['alignment'])
        (out/'sparse').mkdir();shutil.copytree(parent/'sfm/dataset',out/'sparse/dataset')
        dataset=out/'sparse/dataset';(dataset/'training-supported.ply').write_bytes(seed)
        scene=json.loads((dataset/'transforms_train.json').read_text());scene['ply_file_path']='training-supported.ply';write(dataset/'transforms_train.json',scene)
        result.update(initialization=metadata,seedSHA256=hashlib.sha256(seed).hexdigest(),status='training',baselineRandomSeedPoints=2000)
        write(out/'result.json',result)
        result['arm']=train_arm(out,'sparse',binary,args.steps,args.resolution,args.seed)
        baseline=source['arms']['sfm']['evaluation'];candidate=result['arm']['evaluation'];expected=source['evaluationIndices']
        if [r['frameIndex'] for r in baseline]!=expected or [r['frameIndex'] for r in candidate]!=expected:raise ValueError('initialization_evaluation_incomplete')
        rows=[]
        for old,new in zip(baseline,candidate):
            idx=old['frameIndex'];stem=f'{idx:05d}'
            with Image.open(parent/'sfm/artifacts'/f'eval_{args.steps}'/f'{stem}.png') as im:a=im.convert('RGB');size=im.size
            with Image.open(out/'sparse/artifacts'/f'eval_{args.steps}'/f'{stem}.png') as im:b=im.convert('RGB')
            with Image.open(dataset/'images'/f'{stem}.jpg') as im:truth=im.convert('RGB').resize(size,Image.Resampling.LANCZOS)
            panel=Image.new('RGB',(size[0]*3,size[1]));panel.paste(truth,(0,0));panel.paste(a,(size[0],0));panel.paste(b,(size[0]*2,0));panel.save(out/f'compare-{stem}.png')
            rows.append(dict(frameIndex=idx,randomPSNR=old['psnr'],sparsePSNR=new['psnr'],deltaPSNR=new['psnr']-old['psnr'],randomEdgeF1=old['edgeF1'],sparseEdgeF1=new['edgeF1'],image=f'compare-{stem}.png'))
        result.update(status='complete_development_only',comparison=rows,comparisonColumns=['truth','SfM_random','SfM_training_supported_sparse'])
    except BaseException as exc:
        result.update(status='failed',error=type(exc).__name__+': '+str(exc));raise
    finally:write(out/'result.json',result)


def run_initialization_control(args):
    out=Path(args.output).resolve()
    if out.exists(): raise FileExistsError('initialization_output_exists')
    try:
        _run_initialization_control(args)
    except BaseException as exc:
        if not out.exists():
            out.mkdir(parents=True,exist_ok=False)
            write(out/'result.json',dict(status='failed_preflight',productAccepted=False,
                  error=type(exc).__name__+': '+str(exc)))
        raise


def run(args):
    data, model, output, binary=map(lambda p:Path(p).resolve(),(args.data,args.model,args.output,args.binary))
    receipt=verify_binary_receipt(binary)
    output.mkdir(parents=True,exist_ok=False)
    result=dict(status='preparing',productAccepted=False)
    write(output/'result.json',result)
    try:
        result=prepare(data,model,output,Path(args.baseline).resolve(),args.epoch,args.seed)
    except BaseException as exc:
        result.update(status='failed_prepare',error=type(exc).__name__+': '+str(exc)); write(output/'result.json',result); raise
    result.update(status='prepared',binarySHA256=receipt['binarySHA256'],steps=args.steps,resolution=args.resolution,seed=args.seed,maxSplats=20000,shDegree=0,growthFraction=.5,scaleLossWeight=1e-8,randomSeedPoints=2000,arms={})
    write(output/'result.json',result)
    for arm in ('raw','sfm'):
        print(json.dumps(dict(arm=arm,status='starting',output=str(output))),flush=True)
        try: result['arms'][arm]=train_arm(output,arm,binary,args.steps,args.resolution,args.seed)
        except Exception as exc:
            result.update(status='failed',error=str(exc)); write(output/'result.json',result); raise
        write(output/'result.json',result)
    try:
        result.update(status='complete_development_only',comparison=compare(output,result['arms'],args.steps,result['evaluationIndices']),comparisonColumns=['truth','raw_ARKit','aligned_SfM'])
    except BaseException as exc:
        result.update(status='failed_compare',error=type(exc).__name__+': '+str(exc)); write(output/'result.json',result); raise
    write(output/'result.json',result)
    print(json.dumps(dict(status=result['status'],output=str(output))),flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    for name in ('data','model','output','binary','baseline'): p.add_argument('--'+name,required=True)
    p.add_argument('--epoch',type=int,default=2); p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--resolution',type=int,default=256); p.add_argument('--seed',type=int,default=42)
    p.add_argument('--initialization-control-parent')
    args=p.parse_args()
    (run_initialization_control if args.initialization_control_parent else run)(args)
