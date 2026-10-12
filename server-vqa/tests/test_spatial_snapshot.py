"""Protocol/storage regression; no physical localization accuracy assertion."""
import io
import json
import zipfile
import hashlib
import pytest
from app.spatial_snapshot import snapshot_evidence
from test_spatial_scan import client, package


def diagnostic(epoch=3):
    return dict(schemaVersion=2, time=94575.489036, state='geometryConsistent',
                status='空间几何一致；真实位置精度仍需核对', tracking='normal',
                stableCheckCount=10, candidatePlaneCount=4, restoredAnchorCount=22,
                updatedSavedAnchorCount=4, freshPlaneUpdateCount=0,
                planeUpdateBatchCount=30, consumedPlaneUpdateBatchCount=30,
                lastPlaneUpdateTimestamp=94575.405810666,
                lastConsumedPlaneUpdateTimestamp=94575.405810666,
                events=[dict(time=94569.862849625, state='requested', sourceEpoch=2, newEpoch=epoch),
                        dict(time=94575.455829083, state='geometryConsistent', sourceEpoch=2, newEpoch=epoch)])


def archive(d, persist=False, corrupt_manifest=False):
    with zipfile.ZipFile(io.BytesIO(package())) as z:
        payloads={name:z.read(name) for name in z.namelist()}
    doc=json.loads(payloads['document.json'])
    if persist:
        doc['poses'][0]['epoch']=3
        doc['relocalizationEvents']=d['events']
    payloads['document.json']=json.dumps(doc).encode()
    payloads['restoration-diagnostic.json']=json.dumps(d).encode()
    checksums={name:hashlib.sha256(data).hexdigest() for name,data in payloads.items()}
    if corrupt_manifest:checksums['restoration-diagnostic.json']='0'*64
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,data in payloads.items():z.writestr(name,data)
        z.writestr('manifest.json',json.dumps(dict(checksumsSHA256=checksums)))
    return out.getvalue()


def test_live_diagnostic_does_not_invent_persisted_epoch(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);r=c.post('/spatial-scans/import',content=archive(diagnostic())).json()
    s=r['snapshot'];assert s['persistenceConsistent'] is False
    assert s['missingRestoredPoseEpochs']==[3] and len(s['missingDiagnosticEvents'])==2
    assert r['document']['poses'][0]['epoch']==1
    assert 'relocalizationEvents' not in r['document']
    assert c.get('/spatial-scans/'+r['id']).json()['snapshot']==s


def test_saved_events_and_epoch_are_required_and_hash_identifies_version(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);a=c.post('/spatial-scans/import',content=archive(diagnostic())).json()
    b=c.post('/spatial-scans/import',content=archive(diagnostic(),persist=True)).json()
    assert b['snapshot']['persistenceConsistent'] is True
    assert not b['snapshot']['accuracyAccepted']
    assert a['id']!=b['id'] and a['snapshot']['documentSHA256']!=b['snapshot']['documentSHA256']
    assert c.get('/spatial-scans').json()[0]['id']==b['id']


@pytest.mark.parametrize('field,value',[('time',float('nan')),('time',10**400),('schemaVersion',True),('schemaVersion',1.0),('stableCheckCount',True),('consumedPlaneUpdateBatchCount',31),('events',{})])
def test_invalid_diagnostic_is_rejected_without_import(tmp_path,monkeypatch,field,value):
    c=client(tmp_path,monkeypatch);d=diagnostic();d[field]=value
    assert c.post('/spatial-scans/import',content=archive(d)).status_code==422
    assert c.get('/spatial-scans').json()==[]


def test_diagnostic_checksum_is_not_optional_when_manifest_present(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    assert c.post('/spatial-scans/import',content=archive(diagnostic(),corrupt_manifest=True)).status_code==422


def test_legacy_scan_remains_readable_without_diagnostic(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);r=c.post('/spatial-scans/import',content=package()).json()
    assert r['snapshot']['diagnosticAvailable'] is False
    assert 'persistenceConsistent' not in r['snapshot']


def test_malformed_saved_event_cannot_leave_a_partial_job(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);d=diagnostic();d['events'][0]['sourceEpoch']=[]
    assert c.post('/spatial-scans/import',content=archive(d,persist=True)).status_code==422
    assert list(tmp_path.iterdir())==[]


def test_timeout_evidence_is_persisted_without_claiming_recovery(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);d=diagnostic();d['state']='timeout';d['events'][-1]['state']='timeout'
    r=c.post('/spatial-scans/import',content=archive(d,persist=True)).json()
    assert r['snapshot']['persistenceConsistent'] is True
    assert r['snapshot']['diagnostic']['state']=='timeout'
    assert r['snapshot']['accuracyAccepted'] is False
