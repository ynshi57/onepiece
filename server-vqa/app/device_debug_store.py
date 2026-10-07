"""Bounded local evidence storage; reports distinguish observations from hypotheses."""
from __future__ import annotations
import hashlib
import fcntl
from contextlib import contextmanager
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

LOCK = threading.RLock()
_LOCAL = threading.local()
SESSION_LIMIT = 128 * 1024 * 1024
TOTAL_LIMIT = 512 * 1024 * 1024
MAX_SESSIONS = 100

class StoreError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message

def root() -> Path:
    p = Path(os.environ.get('VQASEE_DEVICE_DEBUG_ROOT', '~/.cache/vqasee/device-debug')).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p

@contextmanager
def locked():
    # Serialize quotas and metadata updates across threads and uvicorn workers.
    with LOCK:
        if getattr(_LOCAL, 'held', False):
            yield
            return
        fd = os.open(root() / '.store.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _LOCAL.held = True
            yield
        finally:
            _LOCAL.held = False
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

def identifier(value: str) -> str:
    try:
        if str(uuid.UUID(value)) != value: raise ValueError()
    except (ValueError, AttributeError):
        raise StoreError(400, 'Invalid identifier')
    return value

def directory(sid: str) -> Path:
    p = root() / identifier(sid)
    if p.is_symlink(): raise StoreError(400, 'Symlink not permitted')
    if not p.is_dir(): raise StoreError(404, 'Session not found')
    return p

def _read(sid):
    try:
        return json.loads((directory(sid) / 'session.json').read_text())
    except (OSError, ValueError):
        raise StoreError(409, 'Session metadata unreadable; original evidence retained')

def _write(path: Path, data: bytes):
    temp = path.with_name('.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def _size(p: Path):
    return sum(f.stat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink())

def _commit(p, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    old = p / 'session.json'
    old_size = old.stat().st_size if old.exists() else 0
    if _size(p) - old_size + len(data) > SESSION_LIMIT or _size(root()) - old_size + len(data) > TOTAL_LIMIT:
        raise StoreError(413, 'Storage capacity reached; delete an old session explicitly')
    _write(old, data)

def create(label, previous=None, device_id=None):
    with locked():
        r = root()
        if sum(p.is_dir() for p in r.iterdir()) >= MAX_SESSIONS: raise StoreError(413, 'Session limit reached')
        if previous: directory(previous)
        sid = str(uuid.uuid4()); p = r / sid; p.mkdir(mode=0o700)
        value = dict(id=sid, label=label, device_id=device_id, previous_session_id=previous, created_at=time.time(),
                     stopped_at=None, stop_reason=None, last_screen_at=None, evidence=[], events=[], report=None)
        try: _commit(p, value)
        except Exception:
            p.rmdir(); raise
        return value

def load(sid):
    with locked():
        v = _read(sid)
        last = v['last_screen_at']
        v['stream_state'] = 'stopped' if v['stopped_at'] else ('waiting' if last is None else ('live' if time.time()-last <= 5 else 'stale'))
        return v

def list_sessions():
    with locked():
        result = []
        for p in root().iterdir():
            if not p.is_dir() or p.is_symlink(): continue
            try:
                v = load(p.name)
                result.append({k: v[k] for k in ('id','label','created_at','stopped_at','stream_state','previous_session_id')})
            except StoreError as e: result.append(dict(id=p.name, issue=e.message))
        return sorted(result, key=lambda x:x.get('created_at', 0), reverse=True)

def add_file(sid, data, kind, filename, timestamp=None):
    with locked():
        v = _read(sid); p = directory(sid)
        if v['stopped_at']: raise StoreError(409, 'Session stopped')
        if len(v['evidence']) >= 3000: raise StoreError(413, 'Evidence count limit reached')
        if _size(p)+len(data) > SESSION_LIMIT or _size(root())+len(data) > TOTAL_LIMIT: raise StoreError(413, 'Storage capacity reached')
        eid = str(uuid.uuid4())
        item = dict(id=eid, kind=kind, filename=filename, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    received_at=time.time(), client_timestamp=timestamp)
        path = p / eid
        _write(path, data)
        v['evidence'].append(item)
        if kind == 'screen': v['last_screen_at'] = item['received_at']
        try: _commit(p, v)
        except Exception:
            path.unlink(missing_ok=True); raise
        return item

def event(sid, item):
    with locked():
        v = _read(sid)
        if v['stopped_at']: raise StoreError(409, 'Session stopped')
        if len(v['events']) >= 5000: raise StoreError(413, 'Event count limit reached')
        item = dict(item, id=str(uuid.uuid4()), received_at=time.time())
        v['events'].append(item); _commit(directory(sid), v)
        return item

def stop(sid, reason):
    with locked():
        v = _read(sid)
        if not v['stopped_at']:
            v.update(stopped_at=time.time(), stop_reason=reason); _commit(directory(sid),v)
        return load(sid)

def file(sid,eid):
    with locked():
        v = _read(sid); identifier(eid)
        item = next((i for i in v['evidence'] if i['id']==eid),None)
        if item is None: raise StoreError(404,'Evidence not found')
        p = directory(sid)/eid
        if p.is_symlink() or not p.is_file(): raise StoreError(409,'Evidence missing')
        if p.stat().st_size != item['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
            raise StoreError(409,'Evidence integrity check failed')
        return p,item

def report(sid, value):
    with locked():
        v = _read(sid)
        evidence = {i['id'] for i in v['evidence']} | {i['id'] for i in v['events']}
        for finding in value['findings']:
            if not set(finding['evidence_ids']).issubset(evidence): raise StoreError(400,'Unknown evidence reference')
            if finding['status']=='confirmed' and not finding['evidence_ids']: raise StoreError(400,'Confirmed finding requires evidence')
        for other in value['retest_session_ids']:
            if other == sid or _read(other).get('previous_session_id') != sid:
                raise StoreError(400, 'Retest must be a different session linked to this source')
        v['report'] = dict(value, updated_at=time.time()); _commit(directory(sid),v)
        return v['report']

def delete(sid):
    with locked(): shutil.rmtree(directory(sid))
