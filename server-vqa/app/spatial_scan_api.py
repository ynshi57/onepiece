"""Local spatial capture import. No reconstructed geometry is fabricated."""
from __future__ import annotations
import io
import hashlib
import json
import math
import os
import uuid
import zipfile
import threading
from collections import OrderedDict
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

def require_local(request: Request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "空间扫描资料仅允许在本机访问")


router = APIRouter(prefix="/spatial-scans", tags=["spatial-scans"], dependencies=[Depends(require_local)])
# Matches SpatialScanStore.maximumArchiveBytes. A legal record can contain
# 110 MiB of photos plus a 64 MiB map and a 64 MiB document; no frames omitted.
LIMIT = 256 * 1024 * 1024
_reconstruction_lock = threading.Lock()
_reconstruction_cache = OrderedDict()


def root() -> Path:
    return Path(os.getenv("VQASEE_SPATIAL_ROOT", "~/.cache/vqasee/spatial-scans")).expanduser().resolve()


def job_path(job: str) -> Path:
    try:
        canonical = str(uuid.UUID(job))
    except ValueError:
        raise HTTPException(404, "扫描不存在")
    base = root()
    directory = base / canonical
    if directory.resolve().parent != base or directory.is_symlink():
        raise HTTPException(404, "扫描目录无效")
    return directory


def finite(values, count=None):
    return isinstance(values, list) and (count is None or len(values) == count) and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def point(value):
    return isinstance(value, dict) and finite([value.get(k) for k in ("x", "y", "z")], 3)


def validate(doc, names):
    if not isinstance(doc, dict) or doc.get("schemaVersion") != 1:
        raise ValueError("空间记录版本不支持")
    for key in ("poses", "surfaces", "keyframes"):
        if not isinstance(doc.get(key), list):
            raise ValueError("空间记录缺少数据列表")
    world_map = doc.get("worldMap")
    if world_map is not None:
        if (not isinstance(world_map, dict) or world_map.get("filename") != "world-map.archive"
                or "world-map.archive" not in names
                or not isinstance(world_map.get("sha256"), str)
                or len(world_map["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in world_map["sha256"])
                or not finite([world_map.get("capturedAt")])
                or not isinstance(world_map.get("referenceEpoch"), int)
                or isinstance(world_map["referenceEpoch"], bool) or world_map["referenceEpoch"] < 0
                or not isinstance(world_map.get("referenceSurfaces"), list)):
            raise ValueError("定位地图元数据或附件无效")
        for surface in world_map["referenceSurfaces"]:
            if not isinstance(surface, dict) or not isinstance(surface.get("vertices"), list) or not all(point(p) for p in surface["vertices"]):
                raise ValueError("定位参考几何无效")
    elif "world-map.archive" in names:
        raise ValueError("定位地图附件缺少元数据")
    restoration = doc.get("relocalizationEvents")
    if restoration is not None:
        if not isinstance(restoration, list) or len(restoration) > 100_000:
            raise ValueError("恢复记录无效")
        for event in restoration:
            if not isinstance(event, dict) or not isinstance(event.get("state"), str) or not finite([event.get("time")]):
                raise ValueError("恢复记录时间或状态无效")
            for field in ('sourceEpoch', 'newEpoch'):
                if field in event and (not isinstance(event[field], int) or isinstance(event[field], bool) or event[field] < 0):
                    raise ValueError("恢复记录定位段无效")
    evidence = doc.get("trackingEvents")
    if evidence is not None:
        if not isinstance(evidence, list) or len(evidence) > 100_000:
            raise ValueError("跟踪事件记录无效")
        for e in evidence:
            if not isinstance(e, dict) or e.get("kind") not in {"started", "trackingChanged", "mappingChanged", "trackingGap", "trackingRecovered", "anchorsRemoved", "stopped", "interrupted", "failed"} or not finite([e.get("time")]) or e["time"] < 0 or e.get("timeSource") not in {"frame", "systemUptime"} or not isinstance(e.get("epoch"), int) or isinstance(e["epoch"], bool) or e["epoch"] < 0:
                raise ValueError("跟踪事件时间或类型无效")
    for p in doc["poses"]:
        if not isinstance(p, dict) or not finite([p.get("time")]) or not point(p.get("position")) or not point(p.get("forward")) or not isinstance(p.get("epoch"), int):
            raise ValueError("轨迹记录无效")
    for s in doc["surfaces"]:
        if not isinstance(s, dict) or not isinstance(s.get("vertices"), list) or not all(point(p) for p in s["vertices"]):
            raise ValueError("观测面记录无效")
    for k in doc["keyframes"]:
        if not isinstance(k, dict) or not finite(k.get("cameraToWorld"), 16) or not finite(k.get("intrinsics"), 9):
            raise ValueError("照片相机参数无效")
        name = k.get("imageFile")
        if not isinstance(name, str) or PurePosixPath(name).name != name or "\\" in name or not name.lower().endswith(".jpg") or name not in names:
            raise ValueError("照片缺失或名称无效")
        pose = k.get("pose")
        if not isinstance(pose, dict) or not point(pose.get("position")) or not point(pose.get("forward")) or pose.get("time") != k.get("time") or not isinstance(pose.get("epoch"), int):
            raise ValueError("照片轨迹无效")
        if not finite([k.get("time")]) or not isinstance(k.get("width"), int) or not isinstance(k.get("height"), int) or k["width"] <= 0 or k["height"] <= 0:
            raise ValueError("照片尺寸或时间无效")
        display = [k.get(name) for name in ("displayOrientation", "displayTransform", "displayViewport")]
        if any(v is not None for v in display):
            if display[0] not in {"portrait", "portraitUpsideDown", "landscapeLeft", "landscapeRight"} or not finite(display[1], 6) or not finite(display[2], 2) or min(display[2]) <= 0:
                raise ValueError("照片显示方向参数无效")


@router.get('/splat-viewer.js')
def splat_viewer_script():
    return FileResponse(Path(__file__).with_name('spatial_splat_viewer.js'), media_type='text/javascript')


@router.get("/ui", response_class=HTMLResponse)
def ui():
    return Path(__file__).with_name("spatial_scan.html").read_text()


@router.post("/import")
async def import_scan(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > LIMIT:
            raise HTTPException(413, "完整扫描包超过256MB；原记录不会被修改")
    return await run_in_threadpool(_import_payload, bytes(data))


def _import_payload(data: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 1000 or sum(i.file_size for i in infos) > LIMIT:
                raise HTTPException(413, "解压后扫描包过大")
            names = set()
            for info in infos:
                name = info.filename
                if info.is_dir() or name in names or PurePosixPath(name).name != name or "\\" in name or name in ("", ".", "..") or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError("扫描包含不安全路径")
                if name not in ("document.json", "manifest.json", "world-map.archive", "restoration-diagnostic.json") and not name.lower().endswith(".jpg"):
                    raise ValueError("扫描包含不支持的文件")
                names.add(name)
            doc = json.loads(archive.read("document.json"))
            validate(doc, names)
            # Read before committing to catch CRC corruption and decompression errors.
            payloads = {i.filename: archive.read(i) for i in infos}
            diagnostic = None
            if "restoration-diagnostic.json" in payloads:
                from .spatial_snapshot import validate_diagnostic
                if len(payloads["restoration-diagnostic.json"]) > 65536:
                    raise ValueError("恢复诊断附件过大")
                diagnostic = validate_diagnostic(json.loads(payloads["restoration-diagnostic.json"]))
            if doc.get("worldMap") is not None:
                archive = payloads["world-map.archive"]
                # Match SpatialScanStore.maximumBytes; the complete ZIP is
                # independently bounded by LIMIT in both consumers.
                if not archive or len(archive) > 64 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != doc["worldMap"]["sha256"]:
                    raise ValueError("定位地图校验失败")
            if "manifest.json" in names:
                manifest = json.loads(payloads["manifest.json"])
                checksums = manifest.get("checksumsSHA256")
                if not isinstance(checksums, dict) or set(checksums) != names - {"manifest.json"}:
                    raise ValueError("扫描包校验清单不完整")
                for name, expected in checksums.items():
                    if hashlib.sha256(payloads[name]).hexdigest() != expected:
                        raise ValueError("扫描包校验失败")
            from .spatial_snapshot import snapshot_evidence
            evidence = snapshot_evidence(doc, payloads['document.json'], diagnostic)
    except HTTPException:
        raise
    except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise HTTPException(422, str(exc))
    job = str(uuid.uuid4())
    directory = job_path(job)
    directory.mkdir(parents=True, mode=0o700)
    try:
        for name, content in payloads.items():
            (directory / name).write_bytes(content)
    except OSError:
        import shutil
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(500, "保存扫描失败")
    return {"id": job, "document": doc, "snapshot": evidence, "reconstruction": {"status": "not_available", "message": "此接口返回原始采集记录；空间采集页可建立重建并核对原照片，实验结果尚不代表完整房间"}}


@router.get("")
def list_scans():
    base = root()
    if not base.exists():
        return []
    result = []
    for directory in base.iterdir():
        try:
            uuid.UUID(directory.name)
            doc = json.loads((directory / "document.json").read_text())
            from .spatial_snapshot import snapshot_evidence
            evidence = snapshot_evidence(doc, (directory / "document.json").read_bytes())
            result.append({"id": directory.name, "createdAt": doc.get("createdAt"), "frames": len(doc.get("keyframes", [])),
                           "importedAt": (directory / "document.json").stat().st_mtime, "snapshot": evidence})
        except (ValueError, OSError):
            continue
    return sorted(result, key=lambda entry: entry['importedAt'], reverse=True)


@router.get("/{job}")
def get_scan(job: str):
    try:
        directory = job_path(job)
        document_bytes = (directory / "document.json").read_bytes()
        doc = json.loads(document_bytes)
        from .spatial_snapshot import snapshot_evidence
        diagnostic_path = directory / 'restoration-diagnostic.json'
        diagnostic = json.loads(diagnostic_path.read_bytes()) if diagnostic_path.exists() else None
        evidence = snapshot_evidence(doc, document_bytes, diagnostic)
    except (OSError, ValueError):
        raise HTTPException(404, "扫描不存在或记录损坏")
    return {"id": job, "document": doc, "snapshot": evidence, "reconstruction": {"status": "not_available"}}


@router.get("/{job}/files/{name}")
def file(job: str, name: str):
    directory = job_path(job).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or path.suffix.lower() not in (".jpg", ".json") or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, media_type="application/json" if path.suffix == ".json" else "image/jpeg")


@router.get("/{job}/geometry")
async def geometry(job: str):
    from .spatial_geometry import diagnose
    doc = get_scan(job)["document"]
    if len(doc["surfaces"]) > 128 or any(len(s["vertices"]) > 256 for s in doc["surfaces"]):
        raise HTTPException(422, "本次观测面过多，几何诊断尚不支持此规模；原始数据仍可查看")
    return await run_in_threadpool(diagnose, doc)


@router.post("/{job}/surface-reconstruction")
async def surface_reconstruction(job: str):
    """Real RGB multi-view photometric sampling, not a completed room model."""
    from .spatial_dense import reconstruct_dense
    doc = get_scan(job)["document"]
    if len(doc["keyframes"]) > 240:
        raise HTTPException(422, "扫描超过表面重建容量，原始数据仍可查看")
    def compute():
        with _reconstruction_lock:
            return reconstruct_dense(doc, job_path(job))
    try:
        return await run_in_threadpool(compute)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, "表面重建失败：" + str(exc)) from exc


@router.get("/{job}/quality")
async def quality(job: str):
    from .spatial_quality import diagnose
    from .spatial_image_quality import diagnose as image_diagnose
    doc = get_scan(job)["document"]
    if len(doc["poses"]) > 12_000 or len(doc["keyframes"]) > 240:
        raise HTTPException(422, "记录超过本次检查容量，原始数据仍可查看")
    def inspect():
        return {"trajectory": diagnose(doc), "images": image_diagnose(doc, job_path(job))}
    return await run_in_threadpool(inspect)


@router.get("/{job}/line-structure")
async def line_structure(job: str):
    """Local training-view structural lines, never completed room boundaries."""
    from .spatial_splat_dataset import partition_epoch_frames
    directory = job_path(job)
    doc = get_scan(job)['document']
    if len(doc['keyframes']) > 240:
        raise HTTPException(422, '扫描超过本次结构重建容量，原始记录保留')
    from collections import Counter
    counts = Counter(frame['pose']['epoch'] for frame in doc['keyframes'])
    usable = [epoch for epoch,count in counts.items() if count >= 8]
    if not usable:
        raise HTTPException(422, '同一个定位段需要至少8张照片')
    epoch = max(usable)
    training,evaluation = partition_epoch_frames(doc,epoch)
    def compute():
        from .spatial_line_structure import reconstruct_line_structure, validate_line_structure
        with _reconstruction_lock:
            result = reconstruct_line_structure(doc,directory,[i for i,_ in training])
            result['independentPhotoCheck'] = validate_line_structure(
                doc,directory,result,[i for i,_ in evaluation])
        result['epoch'] = epoch
        result['evaluationFrameIndices'] = [i for i,_ in evaluation]
        result['validationScope'] = 'training_multiview_consistency_not_independent_acceptance'
        return result
    try:
        return await run_in_threadpool(compute)
    except (OSError,ValueError,RuntimeError,AttributeError) as exc:
        raise HTTPException(503, '局部结构线未能建立：'+str(exc)+'；原始照片和轨迹仍可核对') from exc


@router.get("/{job}/reconstruction")
async def reconstruction(job: str):
    """Local bounded reconstruction; cache never changes the original scan."""
    from .spatial_reconstruction import ALGORITHM_VERSION
    directory = job_path(job)
    doc = get_scan(job)["document"]
    if len(doc["poses"]) > 12_000 or len(doc["keyframes"]) > 240:
        raise HTTPException(422, "扫描超过本次重建容量，原始照片和轨迹仍可查看")
    # Both content and photo metadata form the cache identity: a changed input
    # must not keep displaying a result from an earlier scan revision.
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode())
    for frame in doc["keyframes"]:
        path = (directory / frame["imageFile"]).resolve()
        if path.parent != directory.resolve() or not path.is_file():
            raise HTTPException(422, "重建照片缺失或路径无效")
        stat = path.stat()
        digest.update(f"{frame['imageFile']}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    identity = (str(directory), ALGORITHM_VERSION, digest.hexdigest())

    def compute():
        from .spatial_reconstruction import reconstruct
        # Bound simultaneous CPU/memory use, including duplicate requests. Only
        # successful results are cached; failures remain retryable and visible.
        with _reconstruction_lock:
            if identity in _reconstruction_cache:
                _reconstruction_cache.move_to_end(identity)
                return _reconstruction_cache[identity]
            result = reconstruct(doc, directory)
            if result.get("status") not in {"failed", "not_available"}:
                _reconstruction_cache[identity] = result
                while len(_reconstruction_cache) > 2:
                    _reconstruction_cache.popitem(last=False)
            return result

    try:
        return await run_in_threadpool(compute)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, "重建失败：" + str(exc)) from exc

_splat_lock = threading.Lock()
_splat_active_runs: set[str] = set()


def _splat_source_digest(directory):
    # Imported immutable photos are checksum-validated; document/manifest identify input.
    digest = hashlib.sha256()
    for name in ('document.json', 'manifest.json'):
        path = directory/name
        if path.is_file(): digest.update(path.read_bytes())
    return digest.hexdigest()


@router.post('/{job}/splat-brush')
def start_splat_brush(job: str):
    """Verified local GPU trainer; unavailable GPU never silently becomes CPU."""
    from .spatial_brush import DEFAULT_BINARY, DEFAULT_EXPERIMENT, verify_binary_receipt, run as train
    from .spatial_splat_cpu import write_json
    try:
        verify_binary_receipt(DEFAULT_BINARY)
    except (OSError,ValueError,KeyError) as exc:
        raise HTTPException(503,'本机高斯训练器未就绪，请运行 setup_mac.sh --with-spatial-brush；'+str(exc)) from exc
    directory=job_path(job)
    doc=get_scan(job)['document']
    if not 8 <= len(doc['keyframes']) <= 240:
        raise HTTPException(422,'训练需要8至240张真实照片')
    from collections import Counter
    epochs=Counter(f['pose']['epoch'] for f in doc['keyframes'])
    usable=[epoch for epoch,count in epochs.items() if count>=8]
    if not usable: raise HTTPException(422,'同一个定位段需要至少8张照片')
    epoch=max(usable)
    if not _splat_lock.acquire(blocking=False):
        raise HTTPException(409,'本机已有高斯训练正在运行')
    run=str(uuid.uuid4()); output=directory/'splat-runs'/run
    try:
        output.parent.mkdir(exist_ok=True)
        write_json(directory/'splat-current.json',dict(run=run,status='initializing',
                   sourceDigest=_splat_source_digest(directory)))
    except Exception:
        _splat_lock.release(); raise
    def work():
        try:
            train(doc,directory,output,DEFAULT_BINARY,epoch=epoch,**DEFAULT_EXPERIMENT)
        except Exception as exc:
            output.mkdir(exist_ok=True)
            write_json(output/'progress.json',dict(status='failed',reason=str(exc)))
        finally:
            _splat_active_runs.discard(run); _splat_lock.release()
    _splat_active_runs.add(run)
    try:
        threading.Thread(target=work,daemon=True,name='local-brush-training').start()
    except Exception:
        _splat_active_runs.discard(run); _splat_lock.release()
        output.mkdir(exist_ok=True)
        write_json(output/'progress.json',dict(status='failed',reason='training_thread_start_failed'))
        raise
    return dict(status='initializing',run=run,epoch=epoch,message='正在本机训练，验证照片不参与训练')


@router.post('/{job}/splat-reference')
def start_splat_reference(job: str):
    """Local CPU experiment. The API never reports a dataset as a trained model."""
    import importlib.util
    if importlib.util.find_spec('torch') is None:
        raise HTTPException(503, '本机缺少高斯训练运行时，请运行 setup_mac.sh --with-spatial-gs')
    directory = job_path(job)
    doc = get_scan(job)['document']
    if not 8 <= len(doc['keyframes']) <= 240:
        raise HTTPException(422, '训练需要8至240张真实照片')
    if not _splat_lock.acquire(blocking=False):
        raise HTTPException(409, '本机已有高斯训练正在运行')
    run = str(uuid.uuid4())
    output = directory/'splat-runs'/run
    pointer = directory/'splat-current.json'
    from .spatial_splat_cpu import write_json
    try:
        output.parent.mkdir(exist_ok=True)
        write_json(pointer, {'run':run, 'status':'initializing',
                             'sourceDigest':_splat_source_digest(directory)})
    except Exception:
        _splat_lock.release()
        raise
    from collections import Counter
    epochs = Counter(f['pose']['epoch'] for f in doc['keyframes'])
    epoch = epochs.most_common(1)[0][0]
    def work():
        try:
            from .spatial_splat_cpu import train
            train(doc, directory, output, epoch=epoch)
        except Exception as exc:
            output.mkdir(exist_ok=True)
            write_json(output/'progress.json', {'status':'failed', 'reason':str(exc)})
        finally:
            _splat_active_runs.discard(run)
            _splat_lock.release()
    _splat_active_runs.add(run)
    try:
        threading.Thread(target=work, daemon=True, name='local-gaussian-training').start()
    except Exception:
        _splat_active_runs.discard(run)
        _splat_lock.release()
        output.mkdir(exist_ok=True)
        write_json(output/'progress.json', {'status':'failed','reason':'training_thread_start_failed'})
        raise
    return {'status':'initializing', 'run':run, 'message':'本机CPU实验训练，尚未获得完成的模型'}


def current_splat_path(job: str) -> Path:
    directory = job_path(job)
    try:
        pointer = json.loads((directory/'splat-current.json').read_text())
        run = str(uuid.UUID(pointer['run']))
    except (OSError, ValueError, KeyError):
        raise HTTPException(404, '还没有高斯训练任务')
    if pointer.get('sourceDigest') != _splat_source_digest(directory):
        raise HTTPException(409, '扫描数据已变化或旧模型缺少来源校验，请重新训练；旧产物保留')
    return directory/'splat-runs'/run


@router.get('/{job}/splat-reference')
def splat_reference_status(job: str):
    output = current_splat_path(job)
    for filename in ['result.json','progress.json']:
        if (output/filename).is_file():
            value = json.loads((output/filename).read_text())
            if value.get('status')=='training' and output.name not in _splat_active_runs:
                return {'status':'failed','reason':'训练被中断，请重新训练；原产物保留'}
            return value
    if output.name not in _splat_active_runs:
        return {'status':'failed','reason':'训练被中断，请重新训练；原产物保留'}
    return {'status':'initializing','message':'正在从训练照片构建初始点，验证照片不参与'}


@router.get('/{job}/splat-reference/files/{name}')
def splat_reference_file(job: str, name: str):
    output = current_splat_path(job).resolve()
    image_prefix = 'evaluation-alpha-' if name.startswith('evaluation-alpha-') else 'evaluation-'
    allowed = name in {'splats.ply','splats.json','result.json'} or (name.startswith(image_prefix) and name.endswith('.png') and name[len(image_prefix):-4].isdigit()) or (name.startswith('heldout-') and name.endswith('.png') and name[8:-4].isdigit())
    path = (output/name).resolve()
    if not allowed or path.parent != output or not path.is_file():
        raise HTTPException(404, '高斯产物不存在')
    return FileResponse(path)
