"""Authenticated device-screen debugging API; never exposed without explicit pairing."""
import hmac
import io
import json
import math
import os
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image
from pydantic import BaseModel, Field, ValidationError
from app import device_debug_store as store

async def authorize(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    from app.device_debug_pairing import is_admin, device_identity
    supplied = request.headers.get('X-Device-Debug-Token', '')
    if is_admin(supplied):
        request.state.device_id = None
        return
    identity = await run_in_threadpool(device_identity, supplied)
    if identity is None:
        if len(os.environ.get('VQASEE_DEVICE_DEBUG_TOKEN', '')) < 16:
            raise HTTPException(503, 'Local debug administrator is not configured')
        raise HTTPException(401, 'Pair token required')
    request.state.device_id = identity['device_id']
    path = request.url.path
    if request.method == 'POST' and path == '/device-debug/sessions': return
    sid = request.path_params.get('sid')
    if request.method != 'POST' or not sid or path.rsplit('/',1)[-1] not in {'screen','events','attachments','stop'}:
        raise HTTPException(403, 'Device credential is upload-only')
    session = await run_in_threadpool(call, store.load, sid)
    if session.get('device_id') != identity['device_id']:
        raise HTTPException(403, 'Session belongs to another device')

router = APIRouter(prefix='/device-debug', tags=['device-debug'], dependencies=[Depends(authorize)])

class SessionInput(BaseModel):
    label: str = Field(default='iPhone debug', max_length=120)
    previous_session_id: str | None = None
class EventInput(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    details: dict = Field(default_factory=dict)
    client_timestamp: float | None = None
class StopInput(BaseModel):
    reason: str = Field(default='user stopped', max_length=500)
class Finding(BaseModel):
    problem: str = Field(max_length=2000)
    root_cause: str = Field(max_length=4000)
    status: Literal['unknown','hypothesis','confirmed']
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    proposal: str = Field(max_length=4000)
class ReportInput(BaseModel):
    summary: str = Field(max_length=4000)
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    retest_session_ids: list[str] = Field(default_factory=list, max_length=100)

async def body(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>limit: raise HTTPException(413,'Request too large')
        data.extend(chunk)
    return bytes(data)
async def model(request, cls):
    try: return cls.model_validate_json(await body(request,64*1024))
    except ValidationError as e: raise HTTPException(422,str(e))
def call(fn,*args):
    try: return fn(*args)
    except store.StoreError as e: raise HTTPException(e.status,e.message)
    except OSError: raise HTTPException(507,'Local storage unavailable; existing evidence retained')

@router.post('/sessions')
async def create(request:Request):
    v=await model(request,SessionInput)
    device_id = request.state.device_id
    if device_id and v.previous_session_id:
        previous = await run_in_threadpool(call,store.load,v.previous_session_id)
        if previous.get('device_id') != device_id: raise HTTPException(403, 'Retest source belongs to another device')
    return await run_in_threadpool(call,store.create,v.label,v.previous_session_id,device_id)
@router.get('/sessions')
def listing(): return call(store.list_sessions)
@router.get('/sessions/{sid}')
def session(sid:str): return call(store.load,sid)
@router.post('/sessions/{sid}/screen')
async def screen(sid:str,request:Request):
    data=await body(request,2*1024*1024)
    def validate_jpeg():
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.format!='JPEG' or im.width*im.height>8_000_000: raise ValueError()
                im.load()
        except Exception: raise HTTPException(422,'Valid JPEG up to 8 megapixels required')
    await run_in_threadpool(validate_jpeg)
    timestamp=request.headers.get('X-Capture-Timestamp')
    try:
        timestamp=float(timestamp) if timestamp is not None else None
        if timestamp is not None and not math.isfinite(timestamp): raise ValueError()
    except ValueError: raise HTTPException(422,'Invalid capture timestamp')
    return await run_in_threadpool(call,store.add_file,sid,data,'screen','screen.jpg',timestamp)
@router.post('/sessions/{sid}/attachments')
async def attachment(sid:str,request:Request):
    filename=request.headers.get('X-Filename','sample.bin')
    if len(filename)>150 or any(c in filename for c in '/\\\x00\r\n') or filename in ('.','..',''):
        raise HTTPException(422,'Filename must be a basename')
    data=await body(request,32*1024*1024)
    if not data: raise HTTPException(422,'Empty attachment')
    return await run_in_threadpool(call,store.add_file,sid,data,'attachment',filename)
@router.post('/sessions/{sid}/events')
async def event(sid:str,request:Request):
    v=await model(request,EventInput)
    try: json.dumps(v.model_dump(),allow_nan=False)
    except ValueError: raise HTTPException(422,'Non-finite event values')
    return await run_in_threadpool(call,store.event,sid,v.model_dump())
@router.post('/sessions/{sid}/stop')
async def stop(sid:str,request:Request):
    v=await model(request,StopInput)
    return await run_in_threadpool(call,store.stop,sid,v.reason)
@router.get('/sessions/{sid}/files/{eid}')
def file(sid:str,eid:str):
    path,item=call(store.file,sid,eid)
    return FileResponse(path,media_type='image/jpeg' if item['kind']=='screen' else 'application/octet-stream',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
@router.put('/sessions/{sid}/report')
async def report(sid:str,request:Request):
    v=await model(request,ReportInput)
    return await run_in_threadpool(call,store.report,sid,v.model_dump())
@router.get('/sessions/{sid}/report')
def get_report(sid:str): return call(store.load,sid)['report']
@router.delete('/sessions/{sid}')
def delete(sid:str):
    call(store.delete,sid)
    return {'deleted':True}
