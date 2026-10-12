"""Resumable bounded Range downloader; only digest-verified files are promoted."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import urllib.request
import urllib.error


def download(url, destination, *, size, sha256, workers=4, chunk_size=4*1024*1024, retries=3, timeout=45, progress=None, transport="urllib"):
    if size <= 0 or not 1 <= workers <= 8 or chunk_size <= 0 or retries < 1 or not re.fullmatch(r'[a-fA-F0-9]{64}', sha256):
        raise ValueError('invalid_download_parameters')
    if transport not in {'urllib','curl'}: raise ValueError('invalid_transport')
    if transport=='curl' and not shutil.which('curl'): raise RuntimeError('curl_not_available')
    destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        if destination.stat().st_size==size and _digest(destination)==sha256.lower(): return destination
        raise ValueError('existing_destination_not_verified')
    parts=destination.with_name(destination.name+'.parts'); parts.mkdir(exist_ok=True)
    identity={'size':size,'sha256':sha256.lower(),'chunkSize':chunk_size,'sourceHash':hashlib.sha256(url.encode()).hexdigest()}
    manifest=parts/'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text())!=identity: raise ValueError('resume_manifest_mismatch')
    manifest.write_text(json.dumps(identity))
    lock=threading.Lock(); completed=0; cancelled=threading.Event()
    def emit(event, **values):
        if progress:
            with lock: progress({'event':event,**values})
    def fetch(index):
        nonlocal completed
        if cancelled.is_set(): raise RuntimeError("download_cancelled")
        start=index*chunk_size; end=min(size,start+chunk_size)-1; expected=end-start+1
        path=parts/f'{index:08d}.part'
        if not (path.exists() and path.stat().st_size==expected):
            temporary=path.with_suffix('.partial')
            for attempt in range(retries):
                if cancelled.is_set(): raise RuntimeError("download_cancelled")
                try:
                    if transport=='curl':
                        _curl_range(url,temporary,start,end,size,timeout,cancelled)
                    else:
                        request=urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}','Accept-Encoding':'identity'})
                        with urllib.request.urlopen(request,timeout=timeout) as response:
                            if response.status!=206: raise ValueError('range_response_required')
                            if response.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}': raise ValueError('invalid_content_range')
                            if response.headers.get('Content-Length')!=str(expected): raise ValueError('invalid_content_length')
                            if response.headers.get('Content-Encoding','identity')!='identity': raise ValueError('unexpected_content_encoding')
                            with temporary.open('wb') as out:
                                remaining=expected
                                while remaining:
                                    if cancelled.is_set(): raise RuntimeError("download_cancelled")
                                    block=response.read(min(65536,remaining))
                                    if not block: raise ValueError('truncated_range')
                                    out.write(block); remaining-=len(block)
                                if response.read(1): raise ValueError('oversized_range')
                                out.flush(); os.fsync(out.fileno())
                    os.replace(temporary,path); break
                except Exception as exc:
                    temporary.unlink(missing_ok=True)
                    if cancelled.is_set(): raise RuntimeError("download_cancelled") from None
                    reason=_safe_reason(exc)
                    emit('chunk_failed',chunkIndex=index,attempt=attempt+1,reason=reason)
                    if attempt+1==retries:
                        cancelled.set()
                        raise RuntimeError(f'chunk_download_failed:{index}:{reason}') from None
                    if cancelled.wait(min(.25*2**attempt,2.)):
                        raise RuntimeError("download_cancelled") from None
        with lock:
            completed+=expected
            if progress: progress({'event':'progress','completedBytes':completed,'totalBytes':size})
        return path
    count=(size+chunk_size-1)//chunk_size
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(fetch,i) for i in range(count)]
        try:
            for future in as_completed(futures): future.result()
        except BaseException:
            cancelled.set()
            for future in futures: future.cancel()
            raise
    staging=parts/'assembled.partial'
    try:
        with staging.open('wb') as out:
            for index in range(count):
                with (parts/f'{index:08d}.part').open('rb') as source:
                    while block:=source.read(1024*1024): out.write(block)
            out.flush(); os.fsync(out.fileno())
        if staging.stat().st_size!=size or _digest(staging)!=sha256.lower(): raise ValueError('final_sha256_mismatch')
        os.replace(staging,destination)
    finally: staging.unlink(missing_ok=True)
    emit('verified',totalBytes=size,sha256=sha256.lower())
    return destination


def _curl_range(url,temporary,start,end,size,timeout,cancelled):
    headers=temporary.with_suffix('.headers')
    expected=end-start+1
    command=['curl','--silent','--show-error','--location','--fail',
             '--connect-timeout',str(min(timeout,15)), '--max-time',str(timeout),
             '--range',f'{start}-{end}','--header','Accept-Encoding: identity',
             '--dump-header',str(headers),'--output',str(temporary),url]
    try:
        process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        while True:
            try:
                process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                if cancelled.is_set():
                    process.kill(); process.communicate()
                    raise RuntimeError('download_cancelled')
        if process.returncode: raise ValueError('curl_transfer_failed')
        # CONNECT and redirect blocks precede the final origin response.
        blocks=re.split(r'\r?\n\r?\n',headers.read_text(errors='replace'))
        blocks=[block for block in blocks if block.startswith('HTTP/')]
        if not blocks: raise ValueError('curl_headers_invalid')
        lines=blocks[-1].splitlines()
        status=lines[0].split()
        if len(status)<2 or status[1]!='206': raise ValueError('range_response_required')
        values={}
        for line in lines[1:]:
            if ':' not in line: raise ValueError('curl_headers_invalid')
            key,value=line.split(':',1); key=key.strip().lower()
            if key in values and key in {'content-range','content-length','content-encoding'}: raise ValueError('curl_headers_invalid')
            values[key]=value.strip()
        if values.get('content-range')!=f'bytes {start}-{end}/{size}': raise ValueError('invalid_content_range')
        if values.get('content-length')!=str(expected): raise ValueError('invalid_content_length')
        if values.get('content-encoding','identity')!='identity': raise ValueError('unexpected_content_encoding')
        if not temporary.exists() or temporary.stat().st_size!=expected: raise ValueError('truncated_range')
        with temporary.open('rb') as stream: os.fsync(stream.fileno())
    finally:
        headers.unlink(missing_ok=True)


def _safe_reason(exc):
    if isinstance(exc,RuntimeError) and str(exc)=='curl_not_available': return 'curl_not_available'
    if isinstance(exc, urllib.error.HTTPError): return f'http_{exc.code}'
    allowed={'range_response_required','invalid_content_range','invalid_content_length',
             'unexpected_content_encoding','truncated_range','oversized_range',
             'curl_transfer_failed','curl_headers_invalid','curl_not_available'}
    if isinstance(exc,ValueError) and str(exc) in allowed: return str(exc)
    if isinstance(exc,TimeoutError): return 'timeout'
    if isinstance(exc,urllib.error.URLError): return 'transport_error'
    if isinstance(exc,OSError): return 'io_error'
    return 'transfer_error'


def _digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024): digest.update(block)
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True); parser.add_argument('--sha256',required=True)
    parser.add_argument('--size',type=int,required=True); parser.add_argument('--output',required=True)
    parser.add_argument('--transport',choices=['urllib','curl'],default='urllib')
    parser.add_argument('--workers',type=int,default=4); parser.add_argument('--chunk-size',type=int,default=4*1024*1024)
    parser.add_argument('--retries',type=int,default=3); parser.add_argument('--timeout',type=float,default=45)
    args=parser.parse_args()
    try: download(args.url,args.output,size=args.size,sha256=args.sha256,workers=args.workers,chunk_size=args.chunk_size,retries=args.retries,timeout=args.timeout,transport=args.transport,progress=lambda item:print(json.dumps(item),flush=True))
    except Exception as exc:
        print(json.dumps({'event':'failed','reason':_safe_reason(exc),'resumeAvailable':True}),flush=True)
        return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
