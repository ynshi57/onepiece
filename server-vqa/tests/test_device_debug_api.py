import io
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from app.device_debug_api import router
from app import device_debug_store as store

TOKEN='test-pair-token-123456789'
@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('VQASEE_DEVICE_DEBUG_ROOT',str(tmp_path/'evidence'))
    monkeypatch.setenv('VQASEE_DEVICE_DEBUG_TOKEN',TOKEN)
    app=FastAPI(); app.include_router(router)
    with TestClient(app,headers={'X-Device-Debug-Token':TOKEN}) as c: yield c

def create(c):
    r=c.post('/device-debug/sessions',json={'label':'phone'})
    assert r.status_code==200,r.text
    return '/device-debug/sessions/'+r.json()['id']
def jpeg():
    b=io.BytesIO(); Image.new('RGB',(20,30),'blue').save(b,format='JPEG'); return b.getvalue()

def test_authentication_closed_by_default(client,monkeypatch):
    assert client.get('/device-debug/sessions',headers={'X-Device-Debug-Token':'wrong'}).status_code==401
    monkeypatch.delenv('VQASEE_DEVICE_DEBUG_TOKEN')
    assert client.get('/device-debug/sessions').status_code==503

def test_evidence_events_report_and_retest(client):
    base=create(client)
    screen=client.post(base+'/screen',content=jpeg(),headers={'X-Capture-Timestamp':'12.3'}).json()
    assert screen['kind']=='screen'
    event=client.post(base+'/events',json={'kind':'capture.disabled','details':{'reason':'camera_paused'}}).json()
    assert client.get(base+'/files/'+screen['id']).content==jpeg()
    retest=client.post('/device-debug/sessions',json={'label':'retest','previous_session_id':base.rsplit('/',1)[1]}).json()
    report={'summary':'Investigating','findings':[{'problem':'disabled','root_cause':'pending validation',
        'status':'hypothesis','evidence_ids':[event['id'],screen['id']],'proposal':'verify lifecycle'}],
        'retest_session_ids':[retest['id']]}
    assert client.put(base+'/report',json=report).status_code==200
    assert client.get(base+'/report').json()['findings'][0]['status']=='hypothesis'
    assert client.get(base).json()['stream_state']=='live'
    assert client.post(base+'/stop',json={'reason':'user'}).status_code==200
    assert client.get(base).json()['stream_state']=='stopped'
    assert client.post(base+'/screen',content=jpeg()).status_code==409

def test_false_confirmation_and_unknown_evidence_rejected(client):
    base=create(client)
    finding={'problem':'p','root_cause':'r','status':'confirmed','proposal':'s','evidence_ids':[]}
    assert client.put(base+'/report',json={'summary':'s','findings':[finding]}).status_code==400
    finding['status']='hypothesis'; finding['evidence_ids']=['unknown']
    assert client.put(base+'/report',json={'summary':'s','findings':[finding]}).status_code==400

def test_paths_sizes_corruption_and_capacity(client,monkeypatch):
    base=create(client)
    assert client.post(base+'/attachments',content=b'x',headers={'X-Filename':'../secret'}).status_code==422
    assert client.post(base+'/screen',content=b'not jpeg').status_code==422
    assert client.post(base+'/screen',content=b'x'*(2*1024*1024+1)).status_code==413
    item=client.post(base+'/attachments',content=b'original',headers={'X-Filename':'sample.zip'}).json()
    sid=base.rsplit('/',1)[1]
    (store.directory(sid)/item['id']).write_bytes(b'changed')
    assert client.get(base+'/files/'+item['id']).status_code==409
    monkeypatch.setattr(store,'SESSION_LIMIT',1)
    assert client.post(base+'/screen',content=jpeg()).status_code==413

def test_staleness_is_explicit_and_delete_removes_files(client,monkeypatch):
    base=create(client)
    assert client.get(base).json()['stream_state']=='waiting'
    item=client.post(base+'/screen',content=jpeg()).json()
    monkeypatch.setattr(store.time,'time',lambda:item['received_at']+10)
    assert client.get(base).json()['stream_state']=='stale'
    assert client.delete(base).status_code==200
    assert client.get(base).status_code==404

def test_corrupt_metadata_is_visible(client):
    base=create(client); sid=base.rsplit('/',1)[1]
    (store.directory(sid)/'session.json').write_text('broken')
    assert client.get(base).status_code==409
    assert client.get('/device-debug/sessions').json()[0]['issue']

def test_concurrent_appends_preserve_all_evidence(client):
    from concurrent.futures import ThreadPoolExecutor
    base=create(client); sid=base.rsplit('/',1)[1]
    with ThreadPoolExecutor(max_workers=4) as pool:
        items=list(pool.map(lambda n:store.add_file(sid,str(n).encode(),'attachment',f'{n}.bin'),range(12)))
    loaded=client.get(base).json()
    assert len(loaded['evidence'])==12
    assert {i['id'] for i in items}=={i['id'] for i in loaded['evidence']}

def test_failed_metadata_commit_keeps_previous_evidence(client,monkeypatch):
    base=create(client); sid=base.rsplit('/',1)[1]
    first=client.post(base+'/attachments',content=b'original').json()
    original=store._commit
    def fail(*args): raise OSError('disk failure')
    monkeypatch.setattr(store,'_commit',fail)
    assert client.post(base+'/attachments',content=b'next').status_code==507
    monkeypatch.setattr(store,'_commit',original)
    assert [e['id'] for e in client.get(base).json()['evidence']]==[first['id']]
    assert len(list(store.directory(sid).iterdir()))==2

def test_event_payload_and_timestamp_limits(client):
    base=create(client)
    assert client.post(base+'/events',content=b'x'*65537).status_code==413
    assert client.post(base+'/screen',content=jpeg(),headers={'X-Capture-Timestamp':'nan'}).status_code==422
    assert client.post(base+'/screen',content=jpeg()[:-30]).status_code==422

def test_retest_cannot_link_self_or_unrelated_session(client):
    base=create(client)
    unrelated=create(client)
    for target in (base,unrelated):
        report={'summary':'Follow-up','retest_session_ids':[target.rsplit('/',1)[1]]}
        assert client.put(base+'/report',json=report).status_code==400
