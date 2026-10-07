"""Protocol/security integration tests; these are not physical iPhone acceptance."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.device_debug_api import router
from app import device_debug_pairing as pairing

ADMIN = 'test-admin-secret-at-least-16'
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('VQASEE_DEVICE_DEBUG_ROOT', str(tmp_path))
    monkeypatch.setenv('VQASEE_DEVICE_DEBUG_TOKEN', ADMIN)
    pairing._PENDING.clear()
    pairing._RECENT.clear()
    app = FastAPI(); app.include_router(router); app.include_router(pairing.router)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client:
        yield client

def approved(c, name='Protocol test device'):
    p = c.post('/device-debug/pairing/requests', json={'device_name':name}).json()
    assert c.get('/device-debug/pairing/requests/'+p['id']).status_code == 401
    assert c.post('/device-debug/pairing/requests/'+p['id']+'/approve').status_code == 401
    assert c.post('/device-debug/pairing/requests/'+p['id']+'/approve', headers={'X-Device-Debug-Token':ADMIN}).status_code == 200
    result = c.get('/device-debug/pairing/requests/'+p['id'], headers={'X-Pairing-Secret':p['request_secret']}).json()
    assert result['code'] == p['code']
    return result

def test_local_bootstrap_and_cross_origin(client):
    assert client.get('/device-debug/local/bootstrap').status_code == 403
    h={'X-VQASee-Local':'1'}
    assert client.get('/device-debug/local/bootstrap',headers=h).json()['admin_token']==ADMIN
    for extra in ({'Origin':'https://evil.example'}, {'Sec-Fetch-Site':'cross-site'}, {'Host':'evil.example'}):
        assert client.get('/device-debug/local/bootstrap',headers=h|extra).status_code==403

def test_approved_device_only_uploads_own_session_and_revocation(client):
    one=approved(client); two=approved(client, 'Second test device')
    h={'X-Device-Debug-Token':one['device_token']}
    other={'X-Device-Debug-Token':two['device_token']}
    admin={'X-Device-Debug-Token':ADMIN}
    assert client.get('/device-debug/pairing/me',headers=h).status_code==200
    s=client.post('/device-debug/sessions',json={'label':'protocol test'},headers=h).json()
    path='/device-debug/sessions/'+s['id']
    assert client.post(path+'/events',json={'kind':'test','details':{}},headers=h).status_code==200
    assert client.post(path+'/events',json={'kind':'test','details':{}},headers=other).status_code==403
    assert client.get('/device-debug/sessions',headers=h).status_code==403
    assert client.get(path,headers=h).status_code==403
    assert client.get('/device-debug/pairing/devices',headers=h).status_code==401
    assert client.delete('/device-debug/pairing/devices/'+one['device_id'],headers=admin).status_code==200
    assert client.post(path+'/events',json={'kind':'test','details':{}},headers=h).status_code==401
    assert client.get(path,headers=admin).status_code==200

def test_denial_expiry_and_request_limits(client,monkeypatch):
    p=client.post('/device-debug/pairing/requests',json={'device_name':'test'}).json()
    path='/device-debug/pairing/requests/'+p['id']
    assert client.post(path+'/deny',headers={'X-Device-Debug-Token':ADMIN}).status_code==200
    assert client.get(path,headers={'X-Pairing-Secret':p['request_secret']}).json()['status']=='denied'
    monkeypatch.setattr(pairing.time,'time',lambda:p['expires_at']+1)
    assert client.get(path,headers={'X-Pairing-Secret':p['request_secret']}).status_code==410
    assert client.post('/device-debug/pairing/requests',json={'device_name':''}).status_code==422

def test_saved_trust_contains_hash_not_token(client):
    result=approved(client)
    saved=(pairing.store.root()/'trusted-devices.json')
    assert result['device_token'] not in saved.read_text()
    assert saved.stat().st_mode & 0o777 == 0o600
    pairing._PENDING.clear()  # Request-memory loss does not discard persistent trust.
    assert client.get('/device-debug/pairing/me',headers={'X-Device-Debug-Token':result['device_token']}).status_code==200

def test_cancel_releases_pending_slot_and_requires_secret(client):
    for _ in range(4):
        p=client.post('/device-debug/pairing/requests',json={'device_name':'test'}).json()
        path='/device-debug/pairing/requests/'+p['id']
        assert client.delete(path).status_code==401
        assert client.delete(path,headers={'X-Pairing-Secret':p['request_secret']}).status_code==200
        assert client.get(path,headers={'X-Pairing-Secret':p['request_secret']}).status_code==410
