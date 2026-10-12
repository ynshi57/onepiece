"""Saved snapshot evidence, separate from live AR diagnostics and accuracy."""
import hashlib
import math


def validate_diagnostic(value):
    if not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion'] not in (1, 2):
        raise ValueError('恢复诊断版本无效')
    def number(v):
        try:
            return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
        except OverflowError:
            return False
    if not number(value.get('time')) or not isinstance(value.get('state'), str):
        raise ValueError('恢复诊断时间或状态无效')
    for field in ('state', 'status', 'tracking'):
        if not isinstance(value.get(field), str) or len(value[field]) > 4096:
            raise ValueError('恢复诊断文案无效')
    for field in ('stableCheckCount', 'candidatePlaneCount', 'restoredAnchorCount', 'updatedSavedAnchorCount'):
        if not isinstance(value.get(field), int) or isinstance(value[field], bool) or value[field] < 0:
            raise ValueError('恢复诊断计数无效')
    if value['schemaVersion'] == 2:
        for field in ('planeUpdateBatchCount', 'consumedPlaneUpdateBatchCount', 'freshPlaneUpdateCount'):
            if not isinstance(value.get(field), int) or isinstance(value[field], bool) or value[field] < 0:
                raise ValueError('恢复诊断更新计数无效')
        if value['consumedPlaneUpdateBatchCount'] > value['planeUpdateBatchCount']:
            raise ValueError('恢复诊断更新计数矛盾')
    for field in ('lastPlaneUpdateTimestamp', 'lastConsumedPlaneUpdateTimestamp'):
        if field in value and value[field] is not None and not number(value[field]):
            raise ValueError('恢复诊断更新时间无效')
    events = value.get('events')
    if not isinstance(events, list) or len(events) > 64:
        raise ValueError('恢复诊断事件无效')
    for event in events:
        if not isinstance(event, dict) or not number(event.get('time')) or not isinstance(event.get('state'), str):
            raise ValueError('恢复诊断事件时间或状态无效')
        for field in ('sourceEpoch', 'newEpoch'):
            if field in event and (not isinstance(event[field], int) or isinstance(event[field], bool) or event[field] < 0):
                raise ValueError('恢复诊断定位段无效')
    return value


def snapshot_evidence(document, document_bytes, diagnostic=None):
    epochs = sorted({p['epoch'] for p in document.get('poses', [])})
    saved_events = document.get('relocalizationEvents') or []
    evidence = dict(sourceScanID=document.get('id'), documentSHA256=hashlib.sha256(document_bytes).hexdigest(),
                    poseEpochs=epochs, persistedRestorationEventCount=len(saved_events),
                    accuracyAccepted=False, diagnosticAvailable=diagnostic is not None)
    if diagnostic is not None:
        validate_diagnostic(diagnostic)
        def identity(event):
            return tuple(event.get(k) for k in ('state', 'time', 'sourceEpoch', 'newEpoch'))
        saved = {identity(e) for e in saved_events}
        missing = [e for e in diagnostic['events'] if identity(e) not in saved]
        missing_epochs = sorted({e['newEpoch'] for e in diagnostic['events']
                                if e.get('state') == 'geometryConsistent' and 'newEpoch' in e and e['newEpoch'] not in epochs})
        evidence.update(diagnostic=diagnostic, missingDiagnosticEvents=missing,
                        missingRestoredPoseEpochs=missing_epochs,
                        persistenceConsistent=not missing and not missing_epochs)
    return evidence
