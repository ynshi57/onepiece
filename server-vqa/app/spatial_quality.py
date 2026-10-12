"""Trajectory consistency diagnostics, not ground-truth drift measurement."""
from __future__ import annotations
import math
import time
from collections import Counter


def diagnose(document, maximum_gap=2.0, maximum_speed=5.0,
             turning_angle_degrees=30.0, turning_displacement=0.20):
    started = time.perf_counter()
    poses = document.get('poses', [])
    events, speeds = [], []
    epochs = Counter()
    valid_times = []
    distance_total = 0.0
    def point(p, key):
        return tuple(float(p[key][a]) for a in ('x','y','z'))
    def valid(p):
        try:
            return math.isfinite(float(p['time'])) and all(math.isfinite(v) for k in ('position','forward') for v in point(p,k))
        except (KeyError,TypeError,ValueError):
            return False
    for index, p in enumerate(poses):
        if not valid(p):
            events.append({'kind':'invalid_pose','sourcePoseIndices':[index]})
            continue
        valid_times.append(float(p['time']))
        epochs[str(p.get('epoch','unknown'))] += 1
        if index == 0 or not valid(poses[index-1]):
            continue
        a = poses[index-1]
        t0,t1 = float(a['time']),float(p['time'])
        dt = t1-t0
        pa,pb = point(a,'position'),point(p,'position')
        distance = math.dist(pa,pb)
        base = {'sourcePoseIndices':[index-1,index], 'startTime':t0,'endTime':t1,
                'epochBefore':a.get('epoch'),'epochAfter':p.get('epoch'),
                'durationSeconds':dt,'displacementMeters':distance}
        if a.get('epoch') != p.get('epoch'):
            events.append(dict(base,kind='epoch_transition'))
            continue
        if dt <= 0:
            events.append(dict(base,kind='non_increasing_time'))
            continue
        speed = distance/dt
        if dt > maximum_gap:
            events.append(dict(base,kind='time_gap',speedMetersPerSecond=speed))
            continue
        speeds.append(speed)
        distance_total += distance
        if speed > maximum_speed:
            events.append(dict(base,kind='speed_anomaly_candidate',speedMetersPerSecond=speed))
        fa,fb = point(a,'forward'),point(p,'forward')
        na,nb = math.sqrt(sum(v*v for v in fa)),math.sqrt(sum(v*v for v in fb))
        if na > 1e-9 and nb > 1e-9:
            angle = math.degrees(math.acos(max(-1,min(1,sum(x*y for x,y in zip(fa,fb))/(na*nb)))))
            if angle >= turning_angle_degrees and distance >= turning_displacement:
                events.append(dict(base,kind='turn_with_translation_candidate',turnAngleDegrees=angle,speedMetersPerSecond=speed))
    ordered = sorted(speeds)
    p95 = ordered[min(len(ordered)-1,math.ceil(.95*len(ordered))-1)] if ordered else None
    origin = valid_times[0] if valid_times else None
    for event in events:
        if origin is not None and 'startTime' in event:
            event['relativeStartSeconds']=event['startTime']-origin
            event['relativeEndSeconds']=event['endTime']-origin
    return {'status':'diagnostics_only','originalPreserved':True,
            'groundTruthDriftMeasured':False,
            'message':'转向伴随位移与速度异常仅为核对候选；手机绕手移动也会产生真实位移，不能据此认定定位漂移。',
            'source':'raw_document_poses','poseCount':len(poses),'validPoseCount':len(valid_times),
            'epochCounts':dict(epochs),'timeOrigin':origin,
            'durationSeconds':max(valid_times)-min(valid_times) if valid_times else 0,
            'withinSegmentPathLengthMeters':distance_total,
            'speedMetersPerSecond':{'sampleCount':len(speeds),'maximum':max(speeds) if speeds else None,'p95':p95},
            'thresholds':{'maximumGapSeconds':maximum_gap,'maximumSpeedMetersPerSecond':maximum_speed,'turnAngleDegrees':turning_angle_degrees,'turnDisplacementMeters':turning_displacement},
            'eventCounts':dict(Counter(e['kind'] for e in events)),'events':events,
            'elapsedMilliseconds':(time.perf_counter()-started)*1000}
