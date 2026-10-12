"""Local official Brush experiment with explicit heldout artifacts, never acceptance."""
import argparse
import hashlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import shutil
import time

import numpy as np
from PIL import Image

from .spatial_splat_dataset import export_brush_dataset, POSE_SOURCES
from .spatial_splat_cpu import write_json

DEFAULT_BINARY=Path(os.environ.get('VQASEE_SPATIAL_BRUSH_BINARY',
    str(Path(__file__).resolve().parents[1]/'models/spatial-brush/target/release/brush_app')))
BRUSH_COMMIT='3edecbb2fe79d3e2c87eeab85b15e0b1dd10d486'
BRUSH_PATCH='native/brush/patches/0001-small-image-header.patch'
# Development configuration selected on real, reused validation images.
# It is not a passed product preset; sparse seeding failures remain explicit.
DEFAULT_EXPERIMENT=dict(steps=5000,resolution=256,
                        initialization='training_sparse',growth_fraction=.5)
# Explicit local experiment ceiling, not a quality/accuracy acceptance gate.
# Default published experiment capacity remains unchanged.
MAX_EXPERIMENT_SPLATS=100000


def _file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_binary_receipt(binary):
    """Require an installed, digest-bound patched Brush binary before training."""
    binary=Path(binary).resolve()
    if binary.name!='brush_app' or binary.parent.name!='release' or not binary.is_file():
        raise ValueError('brush_binary_missing_or_unrecognized_path')
    receipt_path=binary.parent.parent/'brush-install-receipt.json'
    if not receipt_path.is_file():raise ValueError('brush_binary_receipt_missing')
    manifest_path=Path(__file__).resolve().parents[2]/'native/brush/source-manifest.json'
    manifest=json.loads(manifest_path.read_text())
    if (manifest.get('revision')!=BRUSH_COMMIT or manifest.get('patch',{}).get('path')!=BRUSH_PATCH):
        raise ValueError('brush_source_manifest_unrecognized')
    receipt=json.loads(receipt_path.read_text())
    digest=_file_sha256(binary)
    if (receipt.get('sourceCommit')!=BRUSH_COMMIT
            or receipt.get('sourcePatch')!=BRUSH_PATCH
            or receipt.get('sourcePatchSHA256')!=manifest['patch']['sha256']
            or receipt.get('binarySHA256')!=digest
            or receipt.get('binaryPath')!=str(binary)):
        raise ValueError('brush_binary_receipt_mismatch')
    return receipt


def validate_model(path, *, maximum_splats=20000, sh_degree=None):
    """Accept the actual Brush binary float PLY schema, not a header-only file."""
    if not path.is_file():
        raise RuntimeError('brush_final_model_missing')
    data = path.read_bytes()
    marker = b'end_header\n'
    end = data.find(marker, 0, 65536)
    if end < 0:
        raise RuntimeError('brush_model_header_invalid')
    lines = data[:end].decode('ascii').splitlines()
    if lines[:2] != ['ply', 'format binary_little_endian 1.0']:
        raise RuntimeError('brush_model_format_invalid')
    vertices = [line for line in lines if line.startswith('element ')]
    properties = [line.split() for line in lines if line.startswith('property ')]
    required = {'x','y','z','opacity',*(f'rot_{i}' for i in range(4)),
                *(f'scale_{i}' for i in range(3)),*(f'f_dc_{i}' for i in range(3))}
    if len(vertices) != 1 or not vertices[0].startswith('element vertex '):
        raise RuntimeError('brush_model_vertex_schema_invalid')
    count = int(vertices[0].split()[-1])
    if not 0 < maximum_splats <= MAX_EXPERIMENT_SPLATS or not 0 < count <= maximum_splats or any(len(p)!=3 or p[1]!='float' for p in properties):
        raise RuntimeError('brush_model_vertex_schema_invalid')
    names={p[2] for p in properties}
    rest=names-required
    supported_degrees=[degree for degree in range(4) if rest=={f'f_rest_{i}' for i in range(3*((degree+1)**2-1))}]
    if not required.issubset(names) or len(properties)!=len(names) or not supported_degrees:
        raise RuntimeError('brush_model_properties_invalid')
    if sh_degree is not None and sh_degree != supported_degrees[0]:
        raise RuntimeError('brush_model_sh_degree_mismatch')
    body = data[end+len(marker):]
    if len(body) != count*len(properties)*4:
        raise RuntimeError('brush_model_body_truncated')
    if not np.isfinite(np.frombuffer(body,dtype='<f4')).all():
        raise RuntimeError('brush_model_nonfinite')
    return count


def expected_render_size(source_size, resolution):
    ratio = min(1., resolution/max(source_size))
    return tuple(max(1,int(dimension*ratio+.5)) for dimension in source_size)


def edge_metrics(truth,prediction):
    """Full-frame contrast-edge retention, supplementary to full RGB PSNR."""
    def edges(rgb):
        gray=rgb @ np.array([.2126,.7152,.0722])
        padded=np.pad(gray,1,mode='edge')
        horizontal=(padded[:-2,2:]+2*padded[1:-1,2:]+padded[2:,2:]
                    -padded[:-2,:-2]-2*padded[1:-1,:-2]-padded[2:,:-2])/4
        vertical=(padded[2:,:-2]+2*padded[2:,1:-1]+padded[2:,2:]
                  -padded[:-2,:-2]-2*padded[:-2,1:-1]-padded[:-2,2:])/4
        return np.hypot(horizontal,vertical)>=.1
    def dilated(mask):
        h,w=mask.shape; padded=np.pad(mask,2)
        return np.logical_or.reduce([padded[y:y+h,x:x+w] for y in range(5) for x in range(5)])
    target=edges(truth); predicted=edges(prediction)
    target_count=int(target.sum()); predicted_count=int(predicted.sum())
    recall=float((target&dilated(predicted)).sum()/target_count) if target_count else None
    precision=float((predicted&dilated(target)).sum()/predicted_count) if predicted_count else 0.
    f1=(2*recall*precision/(recall+precision) if recall is not None and recall+precision else 0.)
    return dict(referenceContrastEdges=target_count,renderedContrastEdges=predicted_count,
                edgeRecall=recall,edgePrecision=precision,edgeF1=f1,
                edgeStatus='measured' if target_count else 'no_reference_contrast_edges')


def export_viewer_model(path, destination, *, maximum_splats=20000):
    """Convert the official Z-up PLY to the preview's ARKit Y-up coordinates."""
    from .spatial_splat_dataset import WORLD_ROTATION
    count = validate_model(path,maximum_splats=maximum_splats)
    data = path.read_bytes(); end = data.index(b'end_header\n')
    names = [line.split()[-1] for line in data[:end].decode('ascii').splitlines()
             if line.startswith('property ')]
    values = np.frombuffer(data[end+len(b'end_header\n'):],dtype='<f4').reshape(count,len(names))
    fields = {name:values[:,i] for i,name in enumerate(names)}
    means = np.column_stack([fields[k] for k in ('x','y','z')]) @ WORLD_ROTATION[:3,:3]
    q = np.column_stack([fields[f'rot_{i}'] for i in range(4)])
    norms = np.linalg.norm(q,axis=1)
    if (norms<1e-8).any(): raise RuntimeError('brush_model_rotation_invalid')
    w,x,y,z = (q/norms[:,None]).T
    half = np.sqrt(.5)
    rotations = half*np.column_stack((w+x,x-w,y+z,z-y))
    colors = .5+.28209479177387814*np.column_stack([fields[f'f_dc_{i}'] for i in range(3)])
    opacity = 1/(1+np.exp(-np.clip(fields['opacity'],-80,80)))
    model = dict(means=means.tolist(),logScales=np.column_stack([fields[f'scale_{i}'] for i in range(3)]).tolist(),
                 rotations=rotations.tolist(),colors=colors.clip(0,None).tolist(),opacities=opacity.tolist(),
                 colorConvention='SH0_nonnegative_RGB_radiance_may_exceed_one',
                 coordinateConvention='ARKit_Y_up',previewOnly=True,
                 previewRenderer='WebGL2_instanced_Gaussians_black_background_SH0')
    degree=int(round(np.sqrt(1+(len(names)-14)/3)-1))
    if degree>0:
        rest_per_channel=(degree+1)**2-1
        coefficients=np.empty((count,(degree+1)**2,3),dtype=np.float32)
        for channel in range(3):
            coefficients[:,0,channel]=fields[f'f_dc_{channel}']
            for coefficient in range(rest_per_channel):
                coefficients[:,coefficient+1,channel]=fields[f'f_rest_{channel*rest_per_channel+coefficient}']
        model.update(colorConvention='view_dependent_SH_nonnegative_RGB_radiance_may_exceed_one',shDegree=degree,shCoefficients=coefficients.tolist(),shCoordinateConvention='Brush_Z_up',previewRenderer='WebGL2_instanced_Gaussians_black_background_SH'+str(degree))
    write_json(destination,model)
    return model


def export_training_sparse_seed(document,directory,destination,epoch):
    """Initialize from training-only triangulation; no validation RGB or points."""
    from .spatial_reconstruction import reconstruct
    from .spatial_splat_dataset import WORLD_ROTATION, partition_epoch_frames
    training_frames,_=partition_epoch_frames(document,epoch)
    training=[frame for _,frame in training_frames]
    seed=reconstruct({**document,'keyframes':training},Path(directory))
    points=seed.get('points',[])
    if len(points)<16: raise ValueError('insufficient_training_sparse_seed')
    if len(points)>20000: raise ValueError('training_sparse_seed_exceeds_capacity')
    positions=np.array([[p['position'][a] for a in 'xyz'] for p in points]) @ WORLD_ROTATION[:3,:3].T
    colors=np.array([p.get('colorRGB',[128,128,128]) for p in points])/255
    names=['x','y','z','red','green','blue']
    # Plain RGB point PLY lets the official loader initialize scales/rotations.
    header='ply\nformat binary_little_endian 1.0\nelement vertex '+str(len(points))+'\n'
    header+=''.join('property float '+name+'\n' for name in names)+'end_header\n'
    destination.write_bytes(header.encode()+np.column_stack((positions,colors)).astype('<f4').tobytes())
    return len(points)


def attach_reference_views(model,document,dataset):
    """Retain each saved camera's roll and the trainer's actual input calibration."""
    groups=[json.loads((Path(dataset)/name).read_text())['frames']
            for name in ('transforms_train.json','transforms_val.json')]
    views=[]
    for group in groups:
        for calibration in group:
            index=int(Path(calibration['file_path']).stem)
            frame=document['keyframes'][index]
            eye=np.array([frame['pose']['position'][axis] for axis in 'xyz'])
            forward=np.array([frame['pose']['forward'][axis] for axis in 'xyz'])
            views.append(dict(originalFrameIndex=index,eye=eye.tolist(),target=(eye+forward).tolist(),
                              cameraToWorld=frame['cameraToWorld'],intrinsics=frame['intrinsics'],
                              width=frame['width'],height=frame['height'],focusDistanceMeters=1.,
                              renderCalibration={key:calibration[key] for key in ('fl_x','fl_y','cx','cy','w','h')}))
    model['referenceViews']=views
    model['initialView']={**views[0],'source':'actual_first_training_camera'}
    return model


def run(document,directory,output,binary,**options):
    """One GPU experiment per installed trainer, across API and direct CLI runs."""
    binary=Path(binary).resolve()
    verify_binary_receipt(binary)
    with (binary.parent.parent/'brush-training.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('brush_training_resource_busy') from exc
        try:
            return _run(document,directory,output,binary,**options)
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def _run(document, directory, output, binary, *, epoch, steps=1000, resolution=256,initialization='random',growth_fraction=.1,max_splats=20000,pose_source='raw_ARKit_not_independently_optimized',sh_degree=0,scale_loss_weight=1e-8,seed=42):
    output, binary = Path(output), Path(binary).resolve()
    if output.exists(): raise ValueError('brush_output_already_exists')
    receipt=verify_binary_receipt(binary)
    if not 20 <= steps <= 30000 or not 128 <= resolution <= 1024 or not 1000 <= max_splats <= MAX_EXPERIMENT_SPLATS:
        raise ValueError('invalid_brush_experiment_capacity')
    if not np.isfinite(scale_loss_weight) or not 0 <= scale_loss_weight <= 1e-4 or not isinstance(seed,int) or not 0 <= seed <= 255:
        raise ValueError('invalid_brush_regularization_or_seed')
    if sh_degree not in (0,1,2,3):
        raise ValueError('invalid_brush_sh_degree')
    if initialization not in {'random','training_sparse'}:
        raise ValueError('invalid_brush_initialization')
    if not .1 <= growth_fraction <= .5:
        raise ValueError('invalid_brush_growth_fraction')
    output.mkdir(parents=True)
    started = time.monotonic()
    try:
        metadata = export_brush_dataset(document, Path(directory), output/'dataset',
                                        epoch=epoch, maximum_width=640,pose_source=pose_source)
        if initialization=='training_sparse':
            metadata['seedPoints']=export_training_sparse_seed(document,directory,
                                      output/'dataset/training-seed.ply',epoch)
            train_path=output/'dataset/transforms_train.json'
            training_scene=json.loads(train_path.read_text())
            training_scene['ply_file_path']='training-seed.ply'
            write_json(train_path,training_scene)
    except Exception as error:
        write_json(output/'result.json',dict(status='failed',productAccepted=False,
                   reason='dataset_export_failed: '+str(error),
                   elapsedSeconds=time.monotonic()-started))
        raise
    artifacts = output/'artifacts'
    artifacts.mkdir()
    interval = max(10, steps//2)
    command = [str(binary),str((output/'dataset').resolve()),
               '--total-steps',str(steps),'--max-resolution',str(resolution),
               '--max-splats',str(max_splats),'--sh-degree',str(sh_degree),
               '--growth-select-fraction',str(growth_fraction),
               '--scale-loss-weight',str(scale_loss_weight),'--seed',str(seed),
               '--eval-every',str(interval),'--eval-save-to-disk',
               '--export-every',str(interval),'--export-path',str(artifacts.resolve())]
    provenance = dict(status='training',productAccepted=False,epoch=epoch,
                      trainer='Brush_v0.3.0_with_small_image_header_fix',
                      sourceCommit=receipt['sourceCommit'],
                      sourcePatches=[Path(receipt['sourcePatch']).name],
                      sourcePatchSHA256=receipt['sourcePatchSHA256'],
                      binarySHA256=receipt['binarySHA256'],
                      steps=steps,resolution=resolution,maxSplats=max_splats,shDegree=sh_degree,
                      scaleLossWeight=scale_loss_weight,seed=seed,
                      trainFrames=metadata['trainFrames'],evaluationFrames=metadata['evaluationFrames'],
                      evaluationRGBExcludedFromSeedAndTraining=True,
                      initialization=initialization,seedPoints=metadata.get('seedPoints',0),growthFraction=growth_fraction,
                      poseSource=metadata['poseSource'],
                      limitations=[metadata['poseSource'],'unverified_metric_geometry','experiment_not_product_acceptance'])
    if metadata.get('temporalPoseCorrection') is not None:
        provenance['temporalPoseCorrection']=metadata['temporalPoseCorrection']
        provenance['limitations'].append('temporal_correction_components_not_visually_connected')
    write_json(output/'result.json',provenance)
    try:
        with (output/'trainer.log').open('w') as log:
            process = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,
                                     cwd=output,timeout=7200,check=False)
        if process.returncode:
            raise RuntimeError('brush_trainer_exit_'+str(process.returncode))
        model = artifacts/f'export_{steps}.ply'
        provenance['gaussianCount'] = validate_model(model,maximum_splats=max_splats,sh_degree=sh_degree)
        provenance['capacityReached'] = provenance['gaussianCount'] >= max_splats
        viewer_model=export_viewer_model(model,output/'splats.json',maximum_splats=max_splats)
        attach_reference_views(viewer_model,document,output/'dataset')
        write_json(output/'splats.json',viewer_model)
        shutil.copyfile(model,output/'splats.ply')
        evaluation = json.loads((output/'dataset/transforms_val.json').read_text())['frames']
        measured = []
        for frame in evaluation:
            stem = Path(frame['file_path']).stem
            prediction = artifacts/f'eval_{steps}'/f'{stem}.png'
            if not prediction.is_file(): raise RuntimeError('brush_heldout_render_missing_'+stem)
            with Image.open(prediction) as predicted_image:
                prediction_rgb = np.asarray(predicted_image.convert('RGB'),dtype=np.float32)/255
                size = predicted_image.size
            with Image.open(output/'dataset'/frame['file_path']) as truth_image:
                if size != expected_render_size(truth_image.size,resolution):
                    raise RuntimeError('brush_heldout_render_size_invalid_'+stem)
                truth_image = truth_image.convert('RGB').resize(size,Image.Resampling.LANCZOS)
                truth_rgb = np.asarray(truth_image,dtype=np.float32)/255
                error = float(np.mean((truth_rgb-prediction_rgb)**2))
                triptych = Image.new('RGB',(size[0]*2,size[1]),'white')
                triptych.paste(truth_image,(0,0))
                triptych.paste(Image.fromarray((prediction_rgb*255).clip(0,255).astype('uint8')),(size[0],0))
                triptych.save(output/f'heldout-{stem}.png')
            measured.append(dict(originalFrameIndex=int(stem),mse=error,
                                 psnr=float(-10*np.log10(max(error,1e-12))),
                                 image=f'heldout-{stem}.png',width=size[0],height=size[1],
                                 **edge_metrics(truth_rgb,prediction_rgb)))
        provenance.update(status='trained_official_experiment',
                          elapsedSeconds=time.monotonic()-started,
                          evaluation=measured,modelFile=str(model.relative_to(output)),
                          gaussians=provenance['gaussianCount'],
                          evaluationColumns=['heldout_truth','trained'],
                          metricNote='full_RGB_frame_PIL_Lanczos_reference; official trainer metrics retained in log')
        provenance['edgeMetricNote']='full_frame_Sobel_luma_magnitude_0.1_two_pixel_tolerance; supplementary_not_product_acceptance'
        write_json(output/'result.json',provenance)
        return provenance
    except Exception as error:
        provenance.update(status='failed',reason=str(error),elapsedSeconds=time.monotonic()-started)
        write_json(output/'result.json',provenance)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('document','directory','output'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--binary',default=str(DEFAULT_BINARY))
    parser.add_argument('--epoch',type=int,required=True)
    parser.add_argument('--steps',type=int,default=DEFAULT_EXPERIMENT['steps'])
    parser.add_argument('--resolution',type=int,default=DEFAULT_EXPERIMENT['resolution'])
    parser.add_argument('--initialization',choices=['random','training_sparse'],default=DEFAULT_EXPERIMENT['initialization'])
    parser.add_argument('--growth-fraction',type=float,default=DEFAULT_EXPERIMENT['growth_fraction'])
    parser.add_argument('--max-splats',type=int,default=20000)
    parser.add_argument('--sh-degree',type=int,choices=range(4),default=0)
    parser.add_argument('--scale-loss-weight',type=float,default=1e-8)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--pose-source',choices=POSE_SOURCES,default=POSE_SOURCES[0])
    args=parser.parse_args()
    run(json.loads(Path(args.document).read_text()),args.directory,args.output,args.binary,
        epoch=args.epoch,steps=args.steps,resolution=args.resolution,initialization=args.initialization,growth_fraction=args.growth_fraction,max_splats=args.max_splats,pose_source=args.pose_source,sh_degree=args.sh_degree,scale_loss_weight=args.scale_loss_weight,seed=args.seed)
