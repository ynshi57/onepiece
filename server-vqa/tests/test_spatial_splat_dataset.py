"""Consumer format and holdout isolation, not reconstruction-quality acceptance."""
import json
import hashlib
import fcntl
from pathlib import Path
import numpy as np
import pytest
import sys
from PIL import Image
from app.spatial_splat_dataset import export_brush_dataset, WORLD_ROTATION


@pytest.fixture
def scan_images(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    frames = []
    for i in range(8):
        matrix = np.eye(4); matrix[0, 3] = i*.1
        image_name = f'{i}.jpg'
        Image.new('RGB', (128, 96), (i*20, 0, 0)).save(source/image_name)
        frames.append({'imageFile':image_name, 'time':float(i), 'width':128, 'height':96,
                       'intrinsics':np.array([[100.,0,64],[0,100.,48],[0,0,1.]]).ravel(order='F').tolist(),
                       'cameraToWorld':matrix.ravel(order='F').tolist(),
                       'pose':{'epoch':1,'time':float(i),
                               'position':{'x':i*.1,'y':0.,'z':0.},
                               'forward':{'x':0.,'y':0.,'z':-1.}}})
    return {'keyframes':frames},source


def test_brush_explicit_validation_never_enters_training_file(tmp_path,scan_images):
    document,source=scan_images
    output = tmp_path/'brush'
    metadata = export_brush_dataset(document,source,output,epoch=1,maximum_width=128)
    train = json.loads((output/'transforms_train.json').read_text())['frames']
    heldout = json.loads((output/'transforms_val.json').read_text())['frames']
    assert len(train)==7 and len(heldout)==1
    assert {f['file_path'] for f in train}.isdisjoint({f['file_path'] for f in heldout})
    assert heldout[0]['file_path']=='images/00003.jpg'
    assert all((output/f['file_path']).is_file() for f in train+heldout)
    assert not (output/'transforms.json').exists()
    assert not (output/'transforms_eval.json').exists()
    assert not list(output.glob('*.ply'))
    np.testing.assert_allclose(train[0]['transform_matrix'],WORLD_ROTATION)
    assert metadata['evaluationRGBExcludedFromTraining'] is True


def test_zero_exit_without_actual_model_cannot_be_reported_as_trained(tmp_path,scan_images):
    """A real child-process fault test; this is not a mocked visual-quality check."""
    from app.spatial_brush import run
    document,source=scan_images
    binary=tmp_path/'target/release/brush_app'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!'+sys.executable+'\nraise SystemExit(0)\n')
    binary.chmod(0o755)
    manifest=json.loads((Path(__file__).resolve().parents[2]/'native/brush/source-manifest.json').read_text())
    (binary.parent.parent/'brush-install-receipt.json').write_text(json.dumps({
        'sourceCommit':manifest['revision'],'sourcePatch':manifest['patch']['path'],
        'sourcePatchSHA256':manifest['patch']['sha256'],
        'binarySHA256':hashlib.sha256(binary.read_bytes()).hexdigest(),
        'binaryPath':str(binary.resolve()),'rustcVersion':'test-process-only'}))
    output=tmp_path/'failed-run'
    with (binary.parent.parent/'brush-training.lock').open('a') as occupied:
        fcntl.flock(occupied,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='brush_training_resource_busy'):
            run(document,source,output,binary,epoch=1,steps=20,resolution=128)
        assert not output.exists()
    with pytest.raises(RuntimeError,match='brush_final_model_missing'):
        run(document,source,output,binary,epoch=1,steps=20,resolution=128)
    result=json.loads((output/'result.json').read_text())
    assert result['status']=='failed' and result['productAccepted'] is False
    assert result['evaluationFrames']==1
    assert result['reason']=='brush_final_model_missing'


def test_brush_rejects_truncated_and_nonfinite_model(tmp_path):
    from app.spatial_brush import validate_model, expected_render_size
    names=['f_dc_0','f_dc_1','f_dc_2','opacity','rot_0','rot_1','rot_2','rot_3',
           'scale_0','scale_1','scale_2','x','y','z']
    header=('ply\nformat binary_little_endian 1.0\nelement vertex 1\n'+
            ''.join('property float '+name+'\n' for name in names)+'end_header\n').encode()
    path=tmp_path/'model.ply'
    path.write_bytes(header+np.zeros(14,dtype='<f4').tobytes())
    assert validate_model(path)==1
    path.write_bytes(header+np.zeros(13,dtype='<f4').tobytes())
    with pytest.raises(RuntimeError,match='body_truncated'): validate_model(path)
    values=np.zeros(14,dtype='<f4'); values[11]=np.nan
    path.write_bytes(header+values.tobytes())
    with pytest.raises(RuntimeError,match='nonfinite'): validate_model(path)
    assert expected_render_size((640,480),256)==(256,192)
    assert expected_render_size((480,640),256)==(192,256)


def test_brush_viewer_export_preserves_hdr_radiance_and_camera_roll(tmp_path, scan_images):
    from app.spatial_brush import export_viewer_model, attach_reference_views
    names=['x','y','z','f_dc_0','f_dc_1','f_dc_2','opacity','rot_0','rot_1','rot_2','rot_3','scale_0','scale_1','scale_2']
    header=('ply\nformat binary_little_endian 1.0\nelement vertex 1\n'+''.join('property float '+n+'\n' for n in names)+'end_header\n').encode()
    # Real trainer SH0 radiance can exceed one. Clipping before alpha blending
    # changes the resulting pixels, even when the final display is 8-bit RGB.
    values=np.array([0,0,0,5,0,-3,0,1,0,0,0,-3,-3,-3],dtype='<f4')
    source=tmp_path/'hdr.ply';source.write_bytes(header+values.tobytes())
    model=export_viewer_model(source,tmp_path/'viewer.json')
    assert model['colors'][0][0]>1.9
    assert model['colors'][0][1]==.5
    assert model['colors'][0][2]==0
    assert model['previewRenderer'].startswith('WebGL2_')
    document,images=scan_images
    dataset=tmp_path/'dataset';export_brush_dataset(document,images,dataset,epoch=1,maximum_width=128)
    attach_reference_views(model,document,dataset)
    assert len(model['referenceViews'])==8
    heldout=next(v for v in model['referenceViews'] if v['originalFrameIndex']==3)
    assert heldout['cameraToWorld']==document['keyframes'][3]['cameraToWorld']
    assert heldout['renderCalibration']['w']==128


def test_optimized_pose_provenance_reaches_dataset_evidence(tmp_path,scan_images):
    document,source=scan_images
    label='training_only_optimized_evaluation_interpolated'
    output=tmp_path/'optimized'
    metadata=export_brush_dataset(document,source,output,epoch=1,pose_source=label)
    assert metadata['poseSource']==label
    assert json.loads((output/'evidence.json').read_text())['poseSource']==label
    rejected=tmp_path/'invalid'
    with pytest.raises(ValueError,match='invalid_dataset_pose_source'):
        export_brush_dataset(document,source,rejected,epoch=1,pose_source='ground_truth')
    assert not rejected.exists()
