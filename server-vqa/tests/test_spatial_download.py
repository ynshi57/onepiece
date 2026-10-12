"""Real local HTTP transport faults, with no remote downloads."""
import hashlib
import importlib.util
from pathlib import Path
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

spec=importlib.util.spec_from_file_location('verified_download',Path(__file__).resolve().parents[2]/'deploy/spatial/download_verified.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
DATA=bytes(range(256))*32
SHA=hashlib.sha256(DATA).hexdigest()

@pytest.fixture
def transport():
    state={'mode':'ok','requests':[],'disconnected':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            match=re.fullmatch(r'bytes=(\d+)-(\d+)',self.headers.get('Range',''))
            if not match: self.send_error(400); return
            start,end=map(int,match.groups()); state['requests'].append((start,end))
            if state['mode']=='ignore':
                self.send_response(200); self.send_header('Content-Length',str(len(DATA))); self.end_headers(); self.wfile.write(DATA); return
            body=DATA[start:end+1]
            self.send_response(206)
            content_range=f'bytes {start}-{end}/{len(DATA)}'
            if state['mode']=='bad_range': content_range=f'bytes {start+1}-{end}/{len(DATA)}'
            self.send_header('Content-Range',content_range)
            self.send_header('Content-Length',str(len(body)+(1 if state['mode']=='bad_length' else 0)))
            self.end_headers()
            if state['mode']=='disconnect' and not state['disconnected']:
                state['disconnected']=True
                self.wfile.write(body[:10]); self.wfile.flush(); self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
            if state['mode']=='corrupt': body=bytes([body[0]^1])+body[1:]
            self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}/weights?token=SECRET',state
    server.shutdown(); server.server_close(); thread.join()

def run(url,path,**kwargs):
    return module.download(url,path,size=len(DATA),sha256=SHA,chunk_size=1024,workers=1,timeout=2,**kwargs)

def test_verified_atomic_success(transport,tmp_path):
    url,state=transport; output=tmp_path/'model.pth'; events=[]
    run(url,output,progress=events.append)
    assert output.read_bytes()==DATA
    assert events[-1]['event']=='verified'
    assert 'SECRET' not in str(events)
    state['requests'].clear(); run(url,output)
    assert not state['requests']

@pytest.mark.parametrize('mode',['ignore','bad_range','bad_length'])
def test_strict_range_rejection(transport,tmp_path,mode):
    url,state=transport; state['mode']=mode; output=tmp_path/'model.pth'
    with pytest.raises(RuntimeError,match='chunk_download_failed'): run(url,output,retries=1)
    assert not output.exists()
    assert not list((tmp_path/'model.pth.parts').glob('*.part'))

def test_disconnect_retry_recovers(transport,tmp_path):
    url,state=transport; state['mode']='disconnect'; output=tmp_path/'model.pth'
    run(url,output,retries=2)
    assert output.read_bytes()==DATA
    assert state['requests'].count((0,1023))==2

def test_interrupted_download_resumes_valid_length_parts(transport,tmp_path):
    url,state=transport; output=tmp_path/'model.pth'
    # Pre-fetch a real accepted chunk, then simulate interrupted overall run.
    run(url,output)
    output.unlink(); parts=tmp_path/'model.pth.parts'
    for part in parts.glob('*.part'):
        if part.name!='00000000.part': part.unlink()
    state['requests'].clear(); run(url,output)
    assert output.read_bytes()==DATA
    assert (0,1023) not in state['requests']
    assert len(state['requests'])==7

def test_corruption_never_promotes(transport,tmp_path):
    url,state=transport; state['mode']='corrupt'; output=tmp_path/'model.pth'
    with pytest.raises(ValueError,match='final_sha256_mismatch'): run(url,output)
    assert not output.exists()
    assert len(list((tmp_path/'model.pth.parts').glob('*.part')))==8
    assert not (tmp_path/'model.pth.parts/assembled.partial').exists()

def test_resume_identity_must_match(transport,tmp_path):
    url,state=transport; output=tmp_path/'model.pth'; run(url,output); output.unlink()
    with pytest.raises(ValueError,match='resume_manifest_mismatch'):
        module.download(url,output,size=len(DATA),sha256='0'*64,chunk_size=1024)
    assert not output.exists()


def test_permanent_failure_cancels_queue_and_reports_safe_reason(transport,tmp_path):
    url,state=transport; state['mode']='ignore'; output=tmp_path/'model.pth'; events=[]
    with pytest.raises(RuntimeError):
        module.download(url,output,size=len(DATA),sha256=SHA,chunk_size=32,workers=4,retries=2,timeout=2,progress=events.append)
    # At most the original workers can request; pending 252 chunks never run.
    assert 1 <= len(state['requests']) <= 8
    assert any(e['event']=='chunk_failed' and e['reason']=='range_response_required' for e in events)
    assert 'SECRET' not in str(events)
    assert not output.exists()


def test_safe_reason_never_includes_exception_url():
    from urllib.error import HTTPError, URLError
    secret='https://example.test/?token=SECRET'
    assert module._safe_reason(HTTPError(secret,403,'SECRET',{},None))=='http_403'
    assert module._safe_reason(URLError(secret))=='transport_error'
    assert module._safe_reason(ValueError(secret))=='transfer_error'


@pytest.mark.parametrize('mode',['ok','bad_range'])
def test_curl_transport_strict_response(transport,tmp_path,mode):
    import shutil
    if not shutil.which('curl'): pytest.skip('curl unavailable')
    url,state=transport; state['mode']=mode; output=tmp_path/'model.pth'
    if mode=='ok':
        run(url,output,transport='curl'); assert output.read_bytes()==DATA
    else:
        with pytest.raises(RuntimeError,match='invalid_content_range'):
            run(url,output,transport='curl',retries=1)
        assert not output.exists()
        assert not list((tmp_path/'model.pth.parts').glob('*.part'))
    assert not list((tmp_path/'model.pth.parts').glob('*.headers'))


def test_curl_missing_explicit_failure(monkeypatch,tmp_path):
    monkeypatch.setattr(module.shutil,'which',lambda value:None)
    with pytest.raises(RuntimeError,match='curl_not_available'):
        run('http://invalid.test',tmp_path/'model.pth',transport='curl')
    assert not (tmp_path/'model.pth').exists()
