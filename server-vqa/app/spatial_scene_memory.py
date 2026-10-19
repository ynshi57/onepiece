"""Offline research prototype; not connected to the App and not a 3D map.

build_observation_set(...) -> validated JSON-compatible sidecar; save(path, data)
atomically persists it; load(path) validates again; query(data, label) returns every
matching semantic region, with source frame metadata and explicit unknown 3D.
Model scores are uncalibrated scores, never accuracy or identity evidence.
Version 2 also accepts semanticFrameCandidate with evidence/uncertain, and null
labelID/pixelBBox/modelScore/pixelCount. Its uncertainty is a model self-report,
not validation. Version 1 region-only sidecars remain readable.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = 2


def _keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('Unexpected or missing fields')


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Expected nonempty string')


def _number(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected finite number')
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError('Number outside allowed range')


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Expected integer')


def validate(data):
    """Reject incomplete provenance, invented 3D, and inconsistent frame accounting."""
    _keys(data, 'schemaVersion scanID totalFrameCount requestedFrameIDs frames observations')
    if type(data['schemaVersion']) is not int or data['schemaVersion'] not in (1, SCHEMA_VERSION):
        raise ValueError('Unsupported schemaVersion')
    _text(data['scanID'])
    _integer(data['totalFrameCount'])
    requested = data['requestedFrameIDs']
    if not isinstance(requested, list):
        raise ValueError('Expected frame list')
    for frame_id in requested:
        _text(frame_id)
    if len(set(requested)) != len(requested) or len(requested) > data['totalFrameCount']:
        raise ValueError('Invalid requested frame accounting')
    if not isinstance(data['frames'], list) or not isinstance(data['observations'], list):
        raise ValueError('Expected lists')
    frames = {}
    for frame in data['frames']:
        _keys(frame, 'scanID frameID epoch frameTime capturedAt status failure imageWidth imageHeight')
        if frame['scanID'] != data['scanID']:
            raise ValueError('Frame scan mismatch')
        _text(frame['frameID'])
        if frame['frameID'] in frames:
            raise ValueError('Duplicate frameID')
        frames[frame['frameID']] = frame
        _integer(frame['epoch'])
        _number(frame['frameTime'])
        # Absolute timestamps are optional; absent acquisition time must stay null.
        if frame['capturedAt'] is not None:
            from datetime import datetime
            _text(frame['capturedAt'])
            try:
                timestamp = datetime.fromisoformat(frame['capturedAt'].replace('Z', '+00:00'))
                if timestamp.tzinfo is None:
                    raise ValueError('Timestamp needs timezone')
            except (ValueError, TypeError) as exc:
                raise ValueError('Invalid capturedAt') from exc
        _integer(frame['imageWidth'], 1)
        _integer(frame['imageHeight'], 1)
        if frame['status'] == 'processed':
            if frame['failure'] is not None:
                raise ValueError('Processed frame cannot have failure')
        elif frame['status'] == 'failed':
            _text(frame['failure'])
        else:
            raise ValueError('Unknown frame status')
    if set(frames) != set(requested):
        raise ValueError('Every requested frame needs one outcome')
    seen = set()
    for observation in data['observations']:
        fields = 'scanID observationID frameID epoch frameTime capturedAt modelID labelID label pixelBBox modelScore pixelCount imageWidth imageHeight regionKind entityID worldPosition'
        is_frame = isinstance(observation, dict) and observation.get('regionKind') == 'semanticFrameCandidate'
        if is_frame:
            if data['schemaVersion'] == 1:
                raise ValueError('Version 1 only supports semantic regions')
            fields += ' evidence uncertain'
        _keys(observation, fields)
        for name in ('observationID', 'frameID', 'modelID', 'label'):
            _text(observation[name])
        if observation['observationID'] in seen:
            raise ValueError('Duplicate observationID')
        seen.add(observation['observationID'])
        if observation['scanID'] != data['scanID']:
            raise ValueError('Observation scan mismatch')
        _integer(observation['epoch'])
        _number(observation['frameTime'])
        _integer(observation['imageWidth'], 1)
        _integer(observation['imageHeight'], 1)
        frame = frames.get(observation['frameID'])
        if frame is None or frame['status'] != 'processed':
            raise ValueError('Observation must belong to processed frame')
        for field in ('epoch', 'frameTime', 'capturedAt', 'imageWidth', 'imageHeight'):
            if observation[field] != frame[field]:
                raise ValueError('Observation provenance mismatch')
        if observation['entityID'] is not None or observation['worldPosition'] is not None:
            raise ValueError('Semantic candidates do not establish instances or 3D positions')
        if is_frame:
            for field in ('labelID', 'pixelBBox', 'modelScore', 'pixelCount'):
                if observation[field] is not None:
                    raise ValueError('Frame candidates have no measured geometry or model score')
            _text(observation['evidence'])
            if type(observation['uncertain']) is not bool:
                raise ValueError('uncertain must be boolean; it is a model self-report')
        else:
            _integer(observation['labelID'])
            _number(observation['modelScore'], 0, 1)
            _integer(observation['pixelCount'], 1)
            if observation['pixelCount'] > frame['imageWidth'] * frame['imageHeight']:
                raise ValueError('pixelCount exceeds image size')
            box = observation['pixelBBox']
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError('pixelBBox must be [left, top, right, bottom]')
            for coordinate in box:
                _number(coordinate, 0, 1)
            if box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError('Invalid bounding box')
            if observation['regionKind'] != 'semanticRegionCandidate':
                raise ValueError('Unknown semantic candidate kind')
    return data


def build_observation_set(*, scan_id, total_frame_count, requested_frame_ids, frames, observations):
    """Build from explicit frame outcomes and full provenance observation dictionaries.

    pixelBBox uses normalized top-left coordinates [left, top, right, bottom].
    totalFrameCount is the scan denominator, including unrequested frames.
    """
    data = dict(schemaVersion=SCHEMA_VERSION, scanID=scan_id,
                totalFrameCount=total_frame_count, requestedFrameIDs=requested_frame_ids,
                frames=frames, observations=observations)
    return json.loads(json.dumps(validate(data), allow_nan=False))


def save(path, data):
    validate(data)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', delete=False) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    with open(path, encoding='utf-8') as handle:
        return validate(json.load(handle, object_pairs_hook=unique))


def query(data, label):
    validate(data)
    _text(label)
    candidates = [item for item in data['observations'] if item['label'].casefold() == label.casefold()]
    status = ('candidate_observations' if candidates else
              'label_not_observed' if data['observations'] else
              'no_usable_observations')
    failed = [frame for frame in data['frames'] if frame['status'] == 'failed']
    return dict(scanID=data['scanID'], label=label, observationStatus=status,
                absenceEstablished=False, spatialStatus='unknown3D',
                failedFrames=json.loads(json.dumps(failed)),
                unprocessedFrameCount=data['totalFrameCount'] - len(data['requestedFrameIDs']),
                instanceIdentity='unknown', validationStatus='unverified_model_observations', totalFrameCount=data['totalFrameCount'],
                requestedFrameCount=len(data['requestedFrameIDs']),
                processedFrameCount=sum(f['status'] == 'processed' for f in data['frames']),
                candidates=json.loads(json.dumps(candidates)))
