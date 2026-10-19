"""Synthetic protocol tests; these do not validate AR accuracy or 3DGS."""
import io
import json
import zipfile
import hashlib
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.spatial_scan_api import router


def client(tmp_path, monkeypatch):
    monkeypatch.setenv('VQASEE_SPATIAL_ROOT', str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, client=('127.0.0.1', 50000))


def package(extra=None):
    from PIL import Image
    jpeg = io.BytesIO()
    Image.new('RGB', (2, 2), 'white').save(jpeg, format='JPEG')
    pose = {'time': 1., 'position': {'x': 0, 'y': 0, 'z': 0}, 'forward': {'x': 0, 'y': 0, 'z': -1}, 'epoch': 1}
    doc = {'schemaVersion': 1, 'poses': [pose], 'surfaces': [], 'keyframes': [{'pose': pose, 'time': 1., 'width': 2, 'height': 2, 'cameraToWorld': [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1], 'intrinsics': [1,0,0,0,1,0,1,1,1], 'imageFile': 'frame.jpg'}]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('document.json', json.dumps(doc))
        z.writestr('frame.jpg', jpeg.getvalue())
        if extra: z.writestr(extra, b'unsafe')
    return output.getvalue()


def test_import_preview_and_backend_status(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    script = c.get('/spatial-scans/splat-viewer.js')
    assert script.status_code == 200 and 'SpatialSplatViewer' in script.text
    response = c.post('/spatial-scans/import', content=package(), headers={'Content-Type': 'application/zip'})
    assert response.status_code == 200
    result = response.json()
    assert result['reconstruction']['status'] == 'not_available'
    assert c.get('/spatial-scans/'+result['id']).json()['document']['poses'][0]['epoch'] == 1
    photo = c.get('/spatial-scans/'+result['id']+'/files/frame.jpg')
    assert photo.status_code == 200 and photo.headers['content-type'] == 'image/jpeg'
    assert c.get('/spatial-scans').json()[0]['frames'] == 1


def test_interrupted_training_and_changed_input_are_visible(tmp_path, monkeypatch):
    from app.spatial_scan_api import _splat_source_digest
    import uuid
    c = client(tmp_path, monkeypatch)
    job = c.post('/spatial-scans/import', content=package()).json()['id']
    directory = tmp_path/job
    run = str(uuid.uuid4())
    output = directory/'splat-runs'/run
    output.mkdir(parents=True)
    (directory/'splat-current.json').write_text(json.dumps({'run':run,'sourceDigest':_splat_source_digest(directory)}))
    (output/'progress.json').write_text(json.dumps({'status':'training','iteration':20}))
    status = c.get(f'/spatial-scans/{job}/splat-reference')
    assert status.status_code == 200 and status.json()['status'] == 'failed'
    (output/'result.json').write_text(json.dumps({'status':'trained_reference_experiment'}))
    assert c.get(f'/spatial-scans/{job}/splat-reference').json()['status'] == 'trained_reference_experiment'
    (output/'evaluation-alpha-000.png').write_bytes(b'protocol-fixture')
    assert c.get(f'/spatial-scans/{job}/splat-reference/files/evaluation-alpha-000.png').status_code == 200
    (output/'heldout-00154.png').write_bytes(b'protocol-fixture')
    assert c.get(f'/spatial-scans/{job}/splat-reference/files/heldout-00154.png').status_code == 200
    assert c.get(f'/spatial-scans/{job}/splat-reference/files/heldout-private.png').status_code == 404
    doc = json.loads((directory/'document.json').read_text())
    doc['keyframes'][0]['intrinsics'][0] = 2.
    (directory/'document.json').write_text(json.dumps(doc))
    assert c.get(f'/spatial-scans/{job}/splat-reference').status_code == 409
    assert c.get(f'/spatial-scans/{job}/splat-reference/files/result.json').status_code == 409
    assert (output/'result.json').is_file()


def test_gpu_trainer_missing_has_explicit_failure_and_no_cpu_fallback(tmp_path, monkeypatch):
    from pathlib import Path
    import app.spatial_brush as brush
    monkeypatch.setattr(brush,'DEFAULT_BINARY',Path(tmp_path/'missing/release/brush_app'))
    c=client(tmp_path,monkeypatch)
    job=c.post('/spatial-scans/import',content=package()).json()['id']
    response=c.post(f'/spatial-scans/{job}/splat-brush')
    assert response.status_code==503
    assert '训练器未就绪' in response.json()['detail']
    assert not (tmp_path/job/'splat-current.json').exists()


def test_zip_slip_rejected_before_disk_write(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    response = c.post('/spatial-scans/import', content=package('../escape.jpg'))
    assert response.status_code == 422
    assert list(tmp_path.iterdir()) == []


def test_opaque_world_map_integrity_and_private_access(tmp_path, monkeypatch):
    # Protocol fixture only: these bytes are not an ARWorldMap accuracy test.
    c = client(tmp_path, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(package())) as z:
        files = {name: z.read(name) for name in z.namelist()}
    doc = json.loads(files['document.json'])
    archive = b'opaque-map-protocol-fixture'
    doc['worldMap'] = {'filename': 'world-map.archive',
        'sha256': hashlib.sha256(archive).hexdigest(), 'referenceEpoch': 1,
        'capturedAt': 1., 'mappingStatus': 'mapped', 'referenceSurfaces': []}
    doc['relocalizationEvents'] = [{'state': 'searching', 'time': 2., 'sourceEpoch': 1}]
    def payload(data=archive):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('document.json', json.dumps(doc))
            z.writestr('frame.jpg', files['frame.jpg'])
            z.writestr('world-map.archive', data)
        return out.getvalue()
    response = c.post('/spatial-scans/import', content=payload())
    assert response.status_code == 200
    job = response.json()['id']
    assert (tmp_path / job / 'world-map.archive').read_bytes() == archive
    assert c.get(f'/spatial-scans/{job}/files/world-map.archive').status_code == 404
    before = set(tmp_path.iterdir())
    assert c.post('/spatial-scans/import', content=payload(b'corrupted')).status_code == 422
    assert set(tmp_path.iterdir()) == before
    doc.pop('worldMap')
    assert c.post('/spatial-scans/import', content=payload()).status_code == 422


def test_missing_camera_matrix_rejected(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package())) as source, zipfile.ZipFile(buf, 'w') as target:
        doc = json.loads(source.read('document.json'))
        doc['keyframes'][0]['cameraToWorld'] = []
        target.writestr('document.json', json.dumps(doc))
        target.writestr('frame.jpg', source.read('frame.jpg'))
    assert c.post('/spatial-scans/import', content=buf.getvalue()).status_code == 422


def test_swift_style_manifest_checksum_and_corruption(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(package())) as z:
        files = {name: z.read(name) for name in z.namelist()}
    manifest = {'schemaVersion': 1, 'checksumsSHA256': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    def zipped():
        result = io.BytesIO()
        with zipfile.ZipFile(result, 'w') as z:
            for name, value in files.items(): z.writestr(name, value)
            z.writestr('manifest.json', json.dumps(manifest))
        return result.getvalue()
    assert c.post('/spatial-scans/import', content=zipped()).status_code == 200
    files['frame.jpg'] = b'changed'
    assert c.post('/spatial-scans/import', content=zipped()).status_code == 422


def test_remote_access_to_private_scans_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('VQASEE_SPATIAL_ROOT', str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, client=('192.168.1.50', 50000))
    assert c.get('/spatial-scans').status_code == 403
    assert c.post('/spatial-scans/import', content=package()).status_code == 403
    assert not list(tmp_path.iterdir())


def test_optional_display_metadata_keeps_raw_camera_parameters(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(package())) as z:
        doc = json.loads(z.read('document.json'))
        jpg = z.read('frame.jpg')
    k = doc['keyframes'][0]
    original = list(k['intrinsics']), list(k['cameraToWorld'])
    k.update(displayOrientation='portrait', displayTransform=[0,1,-1,0,1,0], displayViewport=[390,844])
    def payload():
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('document.json', json.dumps(doc))
            z.writestr('frame.jpg', jpg)
        return b.getvalue()
    response = c.post('/spatial-scans/import', content=payload())
    assert response.status_code == 200
    saved = response.json()['document']['keyframes'][0]
    assert (saved['intrinsics'], saved['cameraToWorld']) == original
    assert saved['displayTransform'] == k['displayTransform']
    k['displayViewport'] = [0,844]
    assert c.post('/spatial-scans/import', content=payload()).status_code == 422
    k.pop('displayViewport')
    assert c.post('/spatial-scans/import', content=payload()).status_code == 422


def test_optional_exposure_metadata_is_kept_and_validated(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(package())) as z:
        doc = json.loads(z.read('document.json'))
        jpg = z.read('frame.jpg')
    k = doc['keyframes'][0]
    def payload():
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('document.json', json.dumps(doc))
            z.writestr('frame.jpg', jpg)
        return b.getvalue()
    # Real ARCamera values from an indoor 1/60 s frame.
    k.update(exposureDuration=0.016666, exposureOffset=-0.3, photometricLocked=True)
    response = c.post('/spatial-scans/import', content=payload())
    assert response.status_code == 200
    saved = response.json()['document']['keyframes'][0]
    assert (saved['exposureDuration'], saved['exposureOffset'], saved['photometricLocked']) == (0.016666, -0.3, True)
    for bad in (dict(exposureDuration=0), dict(exposureDuration=2.0), dict(exposureDuration=True),
                dict(exposureOffset='dark'), dict(photometricLocked=1)):
        k.update({'exposureDuration': 0.016666, 'exposureOffset': -0.3, 'photometricLocked': True, **bad})
        assert c.post('/spatial-scans/import', content=payload()).status_code == 422, bad


def test_tracking_event_timestamp_is_not_invented(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(package())) as z:
        doc = json.loads(z.read('document.json'))
        jpg = z.read('frame.jpg')
    def payload():
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('document.json', json.dumps(doc))
            z.writestr('frame.jpg', jpg)
        return b.getvalue()
    legacy = c.post('/spatial-scans/import', content=payload()).json()['document']
    assert 'trackingEvents' not in legacy
    doc['trackingEvents'] = [{'kind':'trackingGap', 'time':1.25, 'timeSource':'frame', 'epoch':1}]
    assert c.post('/spatial-scans/import', content=payload()).json()['document']['trackingEvents'] == doc['trackingEvents']
    doc['trackingEvents'][0]['time'] = -1
    assert c.post('/spatial-scans/import', content=payload()).status_code == 422


def test_reconstruction_cache_tracks_inputs_and_does_not_change_capture(tmp_path, monkeypatch):
    # This tests API/cache behavior only; real reconstruction is tested separately.
    import app.spatial_reconstruction as algorithm
    calls = []
    def reconstruct(doc, directory):
        calls.append(directory)
        return {'status': 'partial', 'points': [], 'reason': 'insufficient_views'}
    monkeypatch.setattr(algorithm, 'reconstruct', reconstruct)
    c = client(tmp_path, monkeypatch)
    imported = c.post('/spatial-scans/import', content=package()).json()
    job = imported['id']
    path = tmp_path / job / 'document.json'
    original = path.read_bytes()
    endpoint = '/spatial-scans/' + job + '/reconstruction'
    assert c.get(endpoint).json()['reason'] == 'insufficient_views'
    assert c.get(endpoint).status_code == 200
    assert len(calls) == 1
    assert path.read_bytes() == original
    image = tmp_path / job / 'frame.jpg'
    image.write_bytes(image.read_bytes() + b' ')
    assert c.get(endpoint).status_code == 200
    assert len(calls) == 2
    monkeypatch.setattr(algorithm, 'ALGORITHM_VERSION', 'test-new-geometry-version')
    assert c.get(endpoint).status_code == 200
    assert len(calls) == 3


def test_reconstruction_failure_remains_visible_and_retryable(tmp_path, monkeypatch):
    import app.spatial_reconstruction as algorithm
    attempts = []
    def reconstruct(doc, directory):
        attempts.append(directory)
        return {'status': 'not_available', 'reason': 'native_sift_not_built'}
    monkeypatch.setattr(algorithm, 'reconstruct', reconstruct)
    c = client(tmp_path, monkeypatch)
    job = c.post('/spatial-scans/import', content=package()).json()['id']
    endpoint = '/spatial-scans/' + job + '/reconstruction'
    for _ in range(2):
        assert c.get(endpoint).json()['reason'] == 'native_sift_not_built'
    assert len(attempts) == 2


def test_reconstruction_capacity_and_missing_photo_are_explicit(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    job = c.post('/spatial-scans/import', content=package()).json()['id']
    path = tmp_path / job / 'document.json'
    doc = json.loads(path.read_text())
    doc['keyframes'] *= 241
    path.write_text(json.dumps(doc))
    endpoint = '/spatial-scans/' + job + '/reconstruction'
    assert c.get(endpoint).status_code == 422
    doc['keyframes'] = doc['keyframes'][:1]
    path.write_text(json.dumps(doc))
    (tmp_path / job / 'frame.jpg').unlink()
    assert c.get(endpoint).status_code == 422


def test_indoor_semantic_masks_and_failure_history_survive_local_import(tmp_path, monkeypatch):
    """Synthetic transport contract only; no private photos or accuracy claim."""
    import base64
    import uuid
    with zipfile.ZipFile(io.BytesIO(package())) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    document = json.loads(files['document.json'])
    document['keyframes'][0]['id'] = 'synthetic-source'
    previous = dict(attemptID=str(uuid.uuid4()), frameID='synthetic-source', frameTime=1.,
                    epoch=1, modelID='synthetic-contract', status='failed', failure='model_unavailable',
                    elapsedSeconds=.02, width=0, height=0, labels='', confidence='', records=[])
    row = dict(attemptID=str(uuid.uuid4()), frameID='synthetic-source', frameTime=1., epoch=1,
               modelID='synthetic-contract', status='processed', elapsedSeconds=.1, width=128, height=128,
               labels=base64.b64encode(bytes([3])*16384).decode(),
               confidence=base64.b64encode(bytes([230])*16384).decode(),
               taxonomy=dict(configSHA256='a'*64, wallLabelID=0, floorLabelID=3, verified=True),
               records=[], previousAttempts=[previous])
    document['indoorSemanticObservations'] = [row]
    files['document.json'] = json.dumps(document).encode()
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    c = client(tmp_path, monkeypatch)
    response = c.post('/spatial-scans/import', content=payload.getvalue())
    assert response.status_code == 200, response.text
    job = response.json()['id']
    assert response.json()['document']['indoorSemanticObservations'] == [row]
    assert c.get('/spatial-scans/'+job).json()['document']['indoorSemanticObservations'] == [row]
    assert (tmp_path/job/'document.json').read_bytes() == files['document.json']
