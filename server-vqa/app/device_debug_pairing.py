"""Local-admin bootstrap and explicitly approved, revocable device credentials."""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
import uuid
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, Response
from app import device_debug_store as store

router = APIRouter(prefix='/device-debug', tags=['device-pairing'])
_PENDING = {}
_RECENT = []
_LOCK = threading.RLock()
DISCOVERY = {'state': 'starting', 'message': '正在启动附近设备发现'}


def admin_token():
    value = os.environ.get('VQASEE_DEVICE_DEBUG_TOKEN', '')
    if len(value) < 16: raise HTTPException(503, 'Local debug administrator is not configured')
    return value


def is_admin(token):
    expected = os.environ.get('VQASEE_DEVICE_DEBUG_TOKEN', '')
    return len(expected) >= 16 and hmac.compare_digest(token.encode(), expected.encode())


def _trusted():
    path = store.root() / 'trusted-devices.json'
    if not path.exists(): return {}
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict): raise ValueError()
        return value
    except (OSError, ValueError):
        raise HTTPException(503, 'Trusted-device storage unreadable; credentials were not reset')


def _save_trusted(value):
    try: store._write(store.root() / 'trusted-devices.json', json.dumps(value, allow_nan=False).encode())
    except OSError: raise HTTPException(507, 'Unable to save device trust')


def device_identity(token):
    if not token or len(token) > 256: return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.locked():
        for device_id, device in _trusted().items():
            if hmac.compare_digest(digest, device['token_hash']):
                return {'device_id': device_id, 'device_name': device['device_name']}
    return None


def require_admin(request):
    if not is_admin(request.headers.get('X-Device-Debug-Token', '')):
        raise HTTPException(401, 'Administrator credential required')


def _loopback(host):
    if host == 'localhost': return True
    try: return ipaddress.ip_address(host).is_loopback
    except ValueError: return False


@router.get('/local/bootstrap')
def bootstrap(request: Request, response: Response):
    peer = request.client.host if request.client else ''
    if not _loopback(peer) or not _loopback(request.url.hostname or ''):
        raise HTTPException(403, 'Local Mac browser required')
    if request.headers.get('X-VQASee-Local') != '1': raise HTTPException(403, 'Local request header required')
    origin = request.headers.get('origin')
    expected = f'{request.url.scheme}://{request.url.netloc}'
    if origin and origin != expected: raise HTTPException(403, 'Origin mismatch')
    if request.headers.get('sec-fetch-site', 'same-origin') not in ('same-origin', 'none'):
        raise HTTPException(403, 'Cross-site bootstrap denied')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Vary'] = 'Origin'
    return {'admin_token': admin_token(), 'discovery': DISCOVERY.copy(), 'server_name': DISCOVERY.get('display_name', 'VQASee Mac')}


@router.post('/pairing/requests')
async def request_pairing(request: Request, response: Response):
    # Limit before parsing, including requests without Content-Length.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 2048: raise HTTPException(413, 'Pair request too large')
    try:
        value = json.loads(data)
        name = value['device_name']
        if not isinstance(name, str) or not name.strip() or len(name) > 80: raise ValueError()
    except (ValueError, KeyError, TypeError): raise HTTPException(422, 'Device name required, up to 80 characters')
    now = time.time()
    peer = request.client.host if request.client else 'unknown'
    with _LOCK:
        for key in list(_PENDING):
            if _PENDING[key]['expires_at'] <= now: del _PENDING[key]
        _RECENT[:] = [entry for entry in _RECENT if entry[1] > now-60]
        if len(_PENDING) >= 32 or sum(p['peer'] == peer and p['status']=='pending' for p in _PENDING.values()) >= 3 or sum(entry[0]==peer for entry in _RECENT) >= 10:
            raise HTTPException(429, 'Too many pairing requests; wait for expiry')
        _RECENT.append((peer, now))
        rid, secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        item = dict(id=rid, request_secret=secret, code=f'{secrets.randbelow(1_000_000):06d}',
                    status='pending', device_name=name.strip(), expires_at=now+300, peer=peer)
        _PENDING[rid] = item
    response.headers['Cache-Control'] = 'no-store'
    return {k:item[k] for k in ('id','request_secret','code','expires_at','status')}


def _pending(rid):
    item = _PENDING.get(rid)
    if not item or item['expires_at'] <= time.time(): raise HTTPException(410, 'Pair request expired; request again')
    return item

@router.delete('/pairing/requests/{rid}')
def cancel(rid: str, request: Request):
    with _LOCK:
        item = _pending(rid)
        if not hmac.compare_digest(request.headers.get('X-Pairing-Secret','').encode(), item['request_secret'].encode()):
            raise HTTPException(401, 'Pair request secret required')
        # If approval raced cancellation, revoke this unclaimed credential too.
        if item['status']=='approved':
            with store.locked():
                devices=_trusted(); devices.pop(item['device_id'], None); _save_trusted(devices)
        del _PENDING[rid]
    return {'status':'cancelled'}


@router.get('/pairing/requests/{rid}')
def poll(rid: str, request: Request, response: Response):
    with _LOCK:
        item = _pending(rid)
        if not hmac.compare_digest(request.headers.get('X-Pairing-Secret', '').encode(), item['request_secret'].encode()):
            raise HTTPException(401, 'Pair request secret required')
        response.headers['Cache-Control'] = 'no-store'
        result = {k:item[k] for k in ('status','code','expires_at')}
        if item['status'] == 'approved':
            # Revocation is effective even if the original request is still being polled.
            if device_identity(item['device_token']) is None: raise HTTPException(401, 'Device trust revoked')
            result.update(device_token=item['device_token'], device_id=item['device_id'])
        return result


@router.get('/pairing/pending')
def pending(request: Request, response: Response):
    require_admin(request); response.headers['Cache-Control'] = 'no-store'
    with _LOCK:
        return [{k:p[k] for k in ('id','device_name','code','expires_at','peer')} for p in _PENDING.values()
                if p['status']=='pending' and p['expires_at']>time.time()]


@router.post('/pairing/requests/{rid}/approve')
def approve(rid: str, request: Request):
    require_admin(request)
    with _LOCK, store.locked():
        item = _pending(rid)
        if item['status'] != 'pending': raise HTTPException(409, 'Request already decided')
        devices = _trusted()
        if len(devices) >= 100: raise HTTPException(413, 'Trusted-device limit reached')
        token, device_id = secrets.token_urlsafe(32), str(uuid.uuid4())
        devices[device_id] = dict(device_name=item['device_name'], token_hash=hashlib.sha256(token.encode()).hexdigest(), approved_at=time.time())
        _save_trusted(devices)
        item.update(status='approved', device_token=token, device_id=device_id)
        return {'status':'approved', 'device_id':device_id}


@router.post('/pairing/requests/{rid}/deny')
def deny(rid: str, request: Request):
    require_admin(request)
    with _LOCK:
        item = _pending(rid)
        if item['status']!='pending': raise HTTPException(409, 'Request already decided')
        item['status']='denied'
    return {'status':'denied'}


@router.get('/pairing/devices')
def devices(request: Request, response: Response):
    require_admin(request); response.headers['Cache-Control']='no-store'
    with store.locked():
        return [dict(device_id=k, device_name=v['device_name'], approved_at=v['approved_at']) for k,v in _trusted().items()]


@router.delete('/pairing/devices/{device_id}')
def revoke(device_id: str, request: Request):
    require_admin(request)
    with store.locked():
        value = _trusted()
        if device_id not in value: raise HTTPException(404, 'Unknown device')
        del value[device_id]; _save_trusted(value)
    return {'revoked':True}


@router.get('/pairing/me')
def me(request: Request, response: Response):
    identity = device_identity(request.headers.get('X-Device-Debug-Token',''))
    if identity is None: raise HTTPException(401, 'Device trust required')
    response.headers['Cache-Control']='no-store'
    return identity
