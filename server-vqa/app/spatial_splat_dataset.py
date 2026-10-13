"""Local, explicit ARKit -> Nerfstudio dataset. This is NOT a trained 3DGS.

Camera axes stay OpenGL; world axes rotate +Y-up to +Z-up. Evaluation images
are in a separate file and excluded from transforms.json entirely.
"""
from pathlib import Path
import hashlib
import json
import shutil
import numpy as np
from PIL import Image
from .spatial_reconstruction import camera, resize_intrinsics

WORLD_ROTATION = np.array([[1.,0.,0.,0.],[0.,0.,-1.,0.],[0.,1.,0.,0.],[0.,0.,0.,1.]])


def partition_epoch_frames(document, epoch):
    """One split for seeding, geometry experiments and official rendering."""
    frames = [(i, f) for i, f in enumerate(document['keyframes'])
              if f['pose']['epoch'] == epoch]
    return ([pair for sequence, pair in enumerate(frames) if sequence % 7 != 3],
            [pair for sequence, pair in enumerate(frames) if sequence % 7 == 3])


POSE_SOURCES=('raw_ARKit_not_independently_optimized','training_only_optimized_evaluation_raw','training_only_optimized_evaluation_interpolated')


def export_brush_dataset(document: dict, source: Path, destination: Path, *, epoch: int,
                         maximum_width: int = 640, pose_source: str = 'raw_ARKit_not_independently_optimized') -> dict:
    """Official Brush reads explicit _val, not Nerfstudio transforms_eval.

    Reuse the exact camera conversion and split; no seed PLY is added implicitly.
    Keep exports outside this directory so a later run cannot load old splats.
    """
    metadata = export_dataset(document, source, destination, epoch=epoch,
                              maximum_width=maximum_width, pose_source=pose_source)
    (destination/'transforms.json').rename(destination/'transforms_train.json')
    (destination/'transforms_eval.json').rename(destination/'transforms_val.json')
    metadata.update(datasetConsumer='Brush_v0.3.0',
                    initialization='official_random_no_input_ply',
                    evaluationFile='transforms_val.json',
                    evaluationRGBExcludedFromTraining=True)
    (destination/'evidence.json').write_text(json.dumps(metadata, indent=2))
    return metadata


def export_dataset(document: dict, source: Path, destination: Path, *, epoch: int,
                   maximum_width: int = 640, pose_source: str = 'raw_ARKit_not_independently_optimized') -> dict:
    if pose_source not in POSE_SOURCES:
        raise ValueError('invalid_dataset_pose_source')
    if destination.exists():
        raise ValueError('dataset_destination_already_exists')
    frames = [(i, f) for i, f in enumerate(document['keyframes']) if f['pose']['epoch'] == epoch]
    if len(frames) < 8:
        raise ValueError('at_least_eight_frames_in_one_epoch_required')
    if not 128 <= maximum_width <= 1920:
        raise ValueError('invalid_image_width')
    # Validate all cameras and actual image dimensions before creating output.
    for _, frame in frames:
        camera(frame)
        image = (source / frame['imageFile']).resolve()
        if image.parent != source.resolve() or not image.is_file():
            raise ValueError('missing_or_invalid_image_path')
        with Image.open(image) as im:
            if im.size != (frame['width'], frame['height']):
                raise ValueError('image_camera_size_mismatch')
    destination.mkdir(parents=True)
    (destination / 'images').mkdir()
    train, evaluation, evidence = [], [], []
    _, heldout_frames = partition_epoch_frames(document, epoch)
    heldout_indices = {index for index, _ in heldout_frames}
    try:
        for sequence, (index, frame) in enumerate(frames):
            cam = camera(frame)
            name = f'images/{index:05d}.jpg'
            with Image.open(source / frame['imageFile']) as im:
                width = min(maximum_width, im.width)
                height = max(1, round(im.height * width / im.width))
                im.convert('RGB').resize((width, height), Image.Resampling.LANCZOS).save(destination/name, quality=95)
            resized_k = resize_intrinsics(cam.k, cam.width, cam.height, width, height)
            exported = dict(file_path=name, transform_matrix=(WORLD_ROTATION @ cam.camera_to_world).tolist(),
                            fl_x=float(resized_k[0,0]), fl_y=float(resized_k[1,1]),
                            cx=float(resized_k[0,2]), cy=float(resized_k[1,2]), w=width, h=height)
            heldout = index in heldout_indices
            (evaluation if heldout else train).append(exported)
            evidence.append(dict(frameIndex=index, time=frame['time'], evaluationOnly=heldout,
                                 sourceSHA256=hashlib.sha256((source/frame['imageFile']).read_bytes()).hexdigest(),
                                 photometricLocked=frame.get('photometricLocked'),
                                 exposureDuration=frame.get('exposureDuration'),
                                 exposureOffset=frame.get('exposureOffset')))
        for name, selected in [('transforms.json', train), ('transforms_eval.json', evaluation)]:
            (destination/name).write_text(json.dumps(dict(camera_model='OPENCV',frames=selected), indent=2))
        metadata = dict(status='dataset_only_not_trained', epoch=epoch, trainFrames=len(train),
                        evaluationFrames=len(evaluation), inputFrames=len(frames),
                        worldFromARKit=WORLD_ROTATION.tolist(), scale='meters_unchanged',
                        cameraConvention='OpenGL_minus_Z_forward_plus_Y_up',
                        poseSource=pose_source,
                        unsupported=['dynamic_masks','glass_geometry','verified_wall_dimensions','per_image_appearance'],
                        photometricLockedFrames=sum(e['photometricLocked'] is True for e in evidence),
                        photometricUnlockedFrames=sum(e['photometricLocked'] is False for e in evidence),
                        photometricUnknownFrames=sum(e['photometricLocked'] is None for e in evidence),
                        frames=evidence)
        if document.get('temporalPoseCorrection') is not None:
            metadata['temporalPoseCorrection']=document['temporalPoseCorrection']
        (destination/'evidence.json').write_text(json.dumps(metadata, indent=2))
        return metadata
    except Exception:
        shutil.rmtree(destination)
        raise
